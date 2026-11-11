// parola-capture: record the microphone and the system audio of a call into two WAV files.
//
//   parola-capture --out-dir DIR --name NAME [--duration SECONDS] [--no-mic] [--no-system]
//                [--levels]
//   parola-capture --stream [--levels]
//
// Writes DIR/NAME.mic.wav (microphone), DIR/NAME.system.wav (system audio, through a Core Audio
// process tap: no virtual driver needed, the audio keeps playing) and DIR/NAME.capture.json.
// Stops after --duration, or on SIGINT / SIGTERM. Requires macOS 14.2+ (decision 0013).
// SIGUSR1 pauses and SIGUSR2 resumes: while paused nothing is written to either track, so the
// two files stay aligned with each other (the pause is cut out of both).
// With --stream, nothing is written to disk: the system audio alone, as 16 kHz mono 16-bit PCM,
// goes to stdout until SIGINT (live subtitles without a recording, decision 0042).
// With --levels, prints the peak level of each track (dBFS) to stderr four times a second:
//   LEVELS {"mic": -31.2, "system": -12.5}
// Audio devices may change during a call: headphones plugged in or taken off, AirPods that drop
// from 48 to 24 kHz when their microphone starts. Each side then restarts on the device in use;
// the files keep the format they started with (the new audio is converted), and the moments the
// restart took are filled with silence, so both files stay on the recording's clock. Every
// change is listed in capture.json under "events" (the kind of device, never its name).

import AVFoundation
import AudioToolbox
import CoreAudio
import Foundation

// The pause signals would terminate the process before their handlers are installed.
signal(SIGUSR1, SIG_IGN)
signal(SIGUSR2, SIG_IGN)
// A reader that goes away (--stream) makes write() fail instead of killing the process.
signal(SIGPIPE, SIG_IGN)

// MARK: - Helpers

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(("parola-capture: " + message + "\n").data(using: .utf8)!)
    exit(1)
}

func check(_ status: OSStatus, _ what: String) {
    if status != noErr { fail("\(what) failed (OSStatus \(status))") }
}

func log(_ message: String) {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
}

func property<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, _ initial: T) -> T {
    var address = AudioObjectPropertyAddress(
        mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<T>.size)
    var value = initial
    check(AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value),
          "reading property \(selector)")
    return value
}

/// Like ``property``, but nil when the object is gone (a device unplugged mid-call).
func tryProperty<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, _ initial: T) -> T? {
    var address = AudioObjectPropertyAddress(
        mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<T>.size)
    var value = initial
    return AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value) == noErr ? value : nil
}

/// "Bluetooth, 24000 Hz": what kind of device, never its name (often the owner's).
func deviceKind(_ device: AudioDeviceID) -> String {
    let kinds: [UInt32: String] = [
        kAudioDeviceTransportTypeBuiltIn: "Built-in", kAudioDeviceTransportTypeBluetooth: "Bluetooth",
        kAudioDeviceTransportTypeBluetoothLE: "Bluetooth LE", kAudioDeviceTransportTypeUSB: "USB",
        kAudioDeviceTransportTypeHDMI: "HDMI", kAudioDeviceTransportTypeDisplayPort: "DisplayPort",
        kAudioDeviceTransportTypeAirPlay: "AirPlay", kAudioDeviceTransportTypeVirtual: "Virtual",
        kAudioDeviceTransportTypeAggregate: "Aggregate",
    ]
    let transport = tryProperty(device, kAudioDevicePropertyTransportType, UInt32(0)) ?? 0
    let rate = tryProperty(device, kAudioDevicePropertyNominalSampleRate, Float64(0)) ?? 0
    return "\(kinds[transport] ?? "Other"), \(Int(rate)) Hz"
}

func transport(_ device: AudioDeviceID) -> UInt32 {
    tryProperty(device, kAudioDevicePropertyTransportType, UInt32(0)) ?? 0
}

func isBluetooth(_ device: AudioDeviceID) -> Bool {
    [kAudioDeviceTransportTypeBluetooth, kAudioDeviceTransportTypeBluetoothLE].contains(transport(device))
}

func nominalRate(_ device: AudioDeviceID) -> Float64 {
    tryProperty(device, kAudioDevicePropertyNominalSampleRate, Float64(0)) ?? 0
}

func allDevices() -> [AudioDeviceID] {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    let system = AudioObjectID(kAudioObjectSystemObject)
    guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &size) == noErr else { return [] }
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(system, &address, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

func hasOutput(_ device: AudioDeviceID) -> Bool {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyStreams, mScope: kAudioObjectPropertyScopeOutput,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    return AudioObjectGetPropertyDataSize(device, &address, 0, nil, &size) == noErr && size > 0
}

func defaultOutput() -> AudioDeviceID {
    tryProperty(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultSystemOutputDevice,
                AudioDeviceID(kAudioObjectUnknown)) ?? AudioDeviceID(kAudioObjectUnknown)
}

/// The device that clocks the system tap. The tap mixes the audio of every process whatever
/// device plays it; the device only keeps time. Bluetooth headphones are a bad clock: they switch
/// profile and rate when their microphone starts, and building an aggregate on them can switch
/// them again, which restarts the microphone, and so on (a loop measured with Beats Studio Buds:
/// 17 restarts in 13 s, both files silent). So a built-in output is used when there is one.
func tapClock() -> AudioDeviceID {
    let output = defaultOutput()
    guard isBluetooth(output) else { return output }
    return allDevices().first { transport($0) == kAudioDeviceTransportTypeBuiltIn && hasOutput($0) } ?? output
}

func listen(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector,
            _ block: @escaping AudioObjectPropertyListenerBlock) -> () -> Void {
    var address = AudioObjectPropertyAddress(
        mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    AudioObjectAddPropertyListenerBlock(object, &address, .main, block)
    return {
        var address = address
        AudioObjectRemovePropertyListenerBlock(object, &address, .main, block)
    }
}

func deviceUID(_ device: AudioDeviceID) -> String {
    let uid: CFString = property(device, kAudioDevicePropertyDeviceUID, "" as CFString)
    return uid as String
}

func deviceName(_ device: AudioDeviceID) -> String {
    let name: CFString = property(device, kAudioObjectPropertyName, "" as CFString)
    return name as String
}

// MARK: - Options

struct Options {
    var outDir = URL(fileURLWithPath: ".")
    var name = "call"
    var duration: Double?
    var mic = true
    var system = true
    var levels = false
    var stream = false
}

func parseOptions() -> Options {
    var o = Options()
    var args = CommandLine.arguments.dropFirst().makeIterator()
    func value(_ flag: String) -> String {
        guard let v = args.next() else { fail("\(flag) needs a value") }
        return v
    }
    while let arg = args.next() {
        switch arg {
        case "--out-dir": o.outDir = URL(fileURLWithPath: value(arg))
        case "--name": o.name = value(arg)
        case "--duration":
            guard let d = Double(value(arg)), d > 0 else { fail("--duration needs seconds") }
            o.duration = d
        case "--no-mic": o.mic = false
        case "--no-system": o.system = false
        case "--levels": o.levels = true
        case "--stream": o.stream = true
        case "-h", "--help":
            print("usage: parola-capture --out-dir DIR --name NAME [--duration S] [--no-mic] [--no-system] [--levels]")
            print("       parola-capture --stream [--levels]")
            print("       parola-capture --outputs | --set-output UID")
            exit(0)
        default: fail("unknown argument \(arg)")
        }
    }
    if o.stream { o.mic = false; o.system = true }  // listening: what the Mac plays, nothing else
    if !o.mic && !o.system { fail("nothing to record") }
    return o
}

// MARK: - Clock and events

// Set on the main queue by the pause signals, read by the capture queues.
let pauseLock = NSLock()
var pausedFlag = false
var isPaused: Bool {
    pauseLock.lock(); defer { pauseLock.unlock() }
    return pausedFlag
}

/// The recording's own time: wall time since the start, without the pauses (they are cut out of
/// both files). A side that lost audio while its device changed is padded up to it.
final class RecordingClock {
    let start = Date()
    private var pausedTotal: TimeInterval = 0
    private var pausedAt: Date?
    private let lock = NSLock()

    func pause() { lock.lock(); if pausedAt == nil { pausedAt = Date() }; lock.unlock() }
    func resume() {
        lock.lock()
        if let at = pausedAt { pausedTotal += Date().timeIntervalSince(at); pausedAt = nil }
        lock.unlock()
    }
    var now: TimeInterval {
        lock.lock(); defer { lock.unlock() }
        let end = pausedAt ?? Date()
        return end.timeIntervalSince(start) - pausedTotal
    }
}

let clock = RecordingClock()
var events: [[String: Any]] = []  // main queue only

func event(_ side: String, _ what: String) {
    let at = (clock.now * 10).rounded() / 10
    events.append(["at": at, "side": side, "what": what])
    log("parola-capture: warning: \(side) audio \(what) at \(at) s")
}

// MARK: - Writers

final class Track {
    private var file: AVAudioFile?  // nil once closed
    private var stream = false  // stdout instead of a file (--stream)
    let format: AVAudioFormat  // the file's, fixed when the recording starts
    var frames: Int64 = 0
    // Peak absolute sample since the last LEVELS line (read and reset on the main queue).
    var peak: Float = 0
    private var converter: AVAudioConverter?
    private var started = false

    init(url: URL, format: AVAudioFormat) throws {
        self.format = format
        file = try AVAudioFile(
            forWriting: url, settings: format.settings, commonFormat: format.commonFormat,
            interleaved: format.isInterleaved)
    }

    /// The audio to stdout as 16 kHz mono 16-bit PCM, what Whisper reads (decision 0042).
    init(streamTo _: FileHandle) {
        format = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000, channels: 1,
                               interleaved: true)!
        stream = true
    }

    func write(_ buffer: AVAudioPCMBuffer) {
        if isPaused { return }
        if !started {
            // Both files start at the recording's start: the microphone engine can take a second
            // or more to deliver its first buffer after the system tap, and the transcripts of the
            // two sides are merged by time.
            started = true
            pad(to: clock.now - Double(buffer.frameLength) / buffer.format.sampleRate)
        }
        if let data = buffer.floatChannelData {
            let interleaved = buffer.format.isInterleaved
            let channels = Int(buffer.format.channelCount)
            let n = Int(buffer.frameLength) * (interleaved ? channels : 1)
            var p = peak
            for c in 0..<(interleaved ? 1 : channels) {
                for i in 0..<n { p = max(p, abs(data[c][i])) }
            }
            peak = p
        }
        if buffer.format.isEqual(format) {
            append(buffer)
        } else if let converted = convert(buffer) {
            append(converted)  // a device changed format mid-call: the file keeps its own
        }
    }

    private func append(_ buffer: AVAudioPCMBuffer) {
        if stream {
            guard let samples = buffer.int16ChannelData else { return }
            var bytes = Int(buffer.frameLength) * MemoryLayout<Int16>.size
            var p = UnsafeRawPointer(samples[0])
            while bytes > 0 {
                let n = Darwin.write(STDOUT_FILENO, p, bytes)
                if n <= 0 { if errno == EINTR { continue }; stream = false; return }  // reader gone
                bytes -= n
                p += n
            }
            frames += Int64(buffer.frameLength)
            return
        }
        guard let file else { return }
        do {
            try file.write(from: buffer)
            frames += Int64(buffer.frameLength)
        } catch {
            log("parola-capture: write error: \(error)")
        }
    }

    private func convert(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        if converter == nil || !converter!.inputFormat.isEqual(buffer.format) {
            converter = AVAudioConverter(from: buffer.format, to: format)
            converter?.downmix = format.channelCount == 1  // both sides of the stereo tap
        }
        guard let converter else { return nil }
        let ratio = format.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 64)
        guard let out = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity) else { return nil }
        var fed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        if let error {
            log("parola-capture: conversion error: \(error)")
            return nil
        }
        return out
    }

    /// Silence up to `seconds` of recording time: the moments a device change cost.
    func pad(to seconds: Double) {
        var missing = Int64(seconds * format.sampleRate) - frames
        guard missing > Int64(format.sampleRate * 0.05) else { return }  // under 50 ms: nothing
        while missing > 0 {
            let n = AVAudioFrameCount(min(missing, Int64(format.sampleRate)))
            guard let silence = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: n) else { return }
            silence.frameLength = n
            let list = UnsafeMutableAudioBufferListPointer(silence.mutableAudioBufferList)
            for b in list { memset(b.mData, 0, Int(b.mDataByteSize)) }
            append(silence)
            missing -= Int64(n)
        }
    }

    /// Writes what AVAudioFile still holds: exit() would drop it (the last ~40 ms).
    func close() { file = nil }

    var seconds: Double { Double(frames) / format.sampleRate }
}

// MARK: - System audio (process tap + private aggregate device)

final class SystemCapture {
    var tapID = AudioObjectID(kAudioObjectUnknown)
    var aggregateID = AudioObjectID(kAudioObjectUnknown)
    var procID: AudioDeviceIOProcID?
    var outputID = AudioDeviceID(kAudioObjectUnknown)
    let queue = DispatchQueue(label: "parola.capture.system")
    var track: Track?
    var outputName = ""
    private var stopListening: [() -> Void] = []  // on the clock device
    private var restartPending = false
    private var lastRestart = Date.distantPast
    private var clockRate: Float64 = 0

    /// Records into ``url``, or streams to stdout when it is nil (--stream).
    func start(url: URL?) throws {
        let format = try create()
        track = try url.map { try Track(url: $0, format: format) } ?? Track(streamTo: .standardOutput)
        outputName = deviceName(defaultOutput())
        // A new default output (headphones in or out): restart only if the clock has to change.
        let system = AudioObjectID(kAudioObjectSystemObject)
        for selector in [kAudioHardwarePropertyDefaultSystemOutputDevice,
                         kAudioHardwarePropertyDefaultOutputDevice] {
            _ = listen(system, selector) { [weak self] _, _ in
                guard let self, tapClock() != self.outputID else { return }
                self.restart("output device changed")
            }
        }
    }

    /// Tap and aggregate clocked by ``tapClock()``; returns the tap's format.
    @discardableResult
    private func create() throws -> AVAudioFormat {
        outputID = tapClock()
        clockRate = nominalRate(outputID)

        // Stereo mixdown of every process; unmuted, so the call keeps playing.
        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        description.uuid = UUID()
        description.name = "parola system tap"
        description.isPrivate = true
        description.muteBehavior = .unmuted
        check(AudioHardwareCreateProcessTap(description, &tapID), "creating the process tap")

        let aggregate: [String: Any] = [
            kAudioAggregateDeviceNameKey: "parola-capture",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: deviceUID(outputID),
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: deviceUID(outputID)]],
            kAudioAggregateDeviceTapListKey: [
                [kAudioSubTapDriftCompensationKey: true, kAudioSubTapUIDKey: description.uuid.uuidString]
            ],
        ]
        check(AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggregateID),
              "creating the aggregate device")

        var asbd: AudioStreamBasicDescription = property(
            tapID, kAudioTapPropertyFormat, AudioStreamBasicDescription())
        guard let format = AVAudioFormat(streamDescription: &asbd) else {
            fail("unsupported tap format")
        }
        check(AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, queue) { [weak self] _, input, _, _, _ in
            guard let track = self?.track, let buffer = AVAudioPCMBuffer(
                pcmFormat: format, bufferListNoCopy: input, deallocator: nil) else { return }
            track.write(buffer)
        }, "creating the IO proc")
        check(AudioDeviceStart(aggregateID, procID), "starting the system capture")
        // The same device changing rate (AirPods: 48 to 24 kHz when their microphone starts) or
        // going away: start again on what is there now.
        stopListening = [
            listen(outputID, kAudioDevicePropertyNominalSampleRate) { [weak self] _, _ in
                guard let self, nominalRate(self.outputID) != self.clockRate else { return }
                self.restart("sample rate changed")
            },
            listen(outputID, kAudioDevicePropertyDeviceIsAlive) { [weak self] _, _ in
                self?.restart("device went away")
            },
        ]
        return format
    }

    private func destroy() {
        stopListening.forEach { $0() }
        stopListening = []
        if aggregateID != kAudioObjectUnknown {
            AudioDeviceStop(aggregateID, procID)
            if let procID { AudioDeviceDestroyIOProcID(aggregateID, procID) }
            queue.sync {}  // let pending buffers be written
            AudioHardwareDestroyAggregateDevice(aggregateID)
            aggregateID = AudioObjectID(kAudioObjectUnknown)
            procID = nil
        }
        if tapID != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tapID)
            tapID = AudioObjectID(kAudioObjectUnknown)
        }
    }

    /// On the main queue; several notifications for one change make a single restart.
    func restart(_ reason: String) {
        guard !restartPending, track != nil else { return }
        restartPending = true
        // Never more than one restart every 2 s: a device settling sends several notifications.
        let wait = max(0.5, 2.0 - Date().timeIntervalSince(lastRestart))
        DispatchQueue.main.asyncAfter(deadline: .now() + wait) { [self] in
            restartPending = false
            lastRestart = Date()
            destroy()
            queue.sync { track?.pad(to: clock.now) }
            for attempt in 1...5 {
                do {
                    try create()
                    event("system", "\(reason): now \(deviceKind(outputID))")
                    return
                } catch {
                    if attempt == 5 { event("system", "\(reason): could not restart (\(error))") }
                    Thread.sleep(forTimeInterval: 0.5)
                }
            }
        }
    }

    func stop() {
        destroy()
    }
}

// MARK: - Microphone

final class MicCapture {
    var engine = AVAudioEngine()
    let queue = DispatchQueue(label: "parola.capture.mic")
    var track: Track?
    private var observer: NSObjectProtocol?
    private var restartPending = false
    private var lastRestart = Date.distantPast
    private var inputID = AudioDeviceID(kAudioObjectUnknown)
    private var inputFormat: AVAudioFormat?

    /// The microphone being recorded (the last one, if it changed: the changes are in events).
    /// Its name diagnoses a silent mic channel: Meet records the mic the person chose in Meet,
    /// this helper records the system default input, and they can be different devices.
    var inputName: String { deviceName(inputID) }

    func start(url: URL) throws {
        let semaphore = DispatchSemaphore(value: 0)
        var granted = false
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: granted = true
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { ok in granted = ok; semaphore.signal() }
            semaphore.wait()
        default: granted = false
        }
        if !granted {
            fail("microphone access denied: allow it in System Settings > Privacy & Security > Microphone")
        }
        track = try Track(url: url, format: engine.inputNode.outputFormat(forBus: 0))
        try attach()
        // A new default microphone (AirPods connecting, a USB headset): follow it.
        _ = listen(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultInputDevice) {
            [weak self] _, _ in self?.restart("input device changed")
        }
    }

    private func attach() throws {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0, let track else {
            throw NSError(domain: "parola-capture", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "no microphone format yet"])
        }
        let queue = self.queue
        inputID = currentInput()
        inputFormat = format
        // installTap is deprecated in macOS 27 but still works; to revisit with its replacement.
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { buffer, _ in
            queue.async { track.write(buffer) }
        }
        try engine.start()
        // AVAudioEngine stops by itself when its device changes format: then start again.
        observer = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
        ) { [weak self] _ in self?.restart("configuration changed") }
    }

    private func detach() {
        if let observer { NotificationCenter.default.removeObserver(observer) }
        observer = nil
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        queue.sync {}
    }

    private func currentInput() -> AudioDeviceID {
        tryProperty(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultInputDevice,
                    AudioDeviceID(kAudioObjectUnknown)) ?? AudioDeviceID(kAudioObjectUnknown)
    }

    /// On the main queue; several notifications for one change make a single restart, and never
    /// more than one every 2 s. The engine is rebuilt only when the default microphone changed:
    /// reopening the same Bluetooth microphone makes it renegotiate, and the format change that
    /// follows would restart it again.
    func restart(_ reason: String) {
        guard !restartPending, track != nil else { return }
        restartPending = true
        let wait = max(0.3, 2.0 - Date().timeIntervalSince(lastRestart))
        DispatchQueue.main.asyncAfter(deadline: .now() + wait) { [self] in
            restartPending = false
            lastRestart = Date()
            let before = (inputID, inputFormat)
            detach()
            let newDevice = currentInput() != before.0
            if newDevice { engine = AVAudioEngine() }  // a new engine takes the new default input
            queue.sync { track?.pad(to: clock.now) }
            for attempt in 1...5 {
                do {
                    try attach()
                    let changed = newDevice || !(inputFormat?.isEqual(before.1) ?? false)
                    if changed {
                        event("mic", "\(reason): now \(deviceKind(inputID))")
                    } else {
                        log("parola-capture: mic \(reason), same device and format: reattached")
                    }
                    return
                } catch {
                    engine.inputNode.removeTap(onBus: 0)
                    if attempt == 5 { event("mic", "\(reason): could not restart (\(error))") }
                    Thread.sleep(forTimeInterval: 0.5)
                    engine = AVAudioEngine()
                }
            }
        }
    }

    func stop() {
        detach()
    }
}

// MARK: - Output devices

/// `--outputs` lists the Mac's output devices as JSON, `--set-output UID` makes one the default
/// output (what Control Center's switcher does): the settings gear in the app. It lives in this
/// helper because the rest of the CoreAudio device code is already here.
func runDeviceCommand() {
    let args = CommandLine.arguments
    guard args.count >= 2, ["--outputs", "--set-output"].contains(args[1]) else { return }
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultOutputDevice,
        mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    let system = AudioObjectID(kAudioObjectSystemObject)
    if args[1] == "--outputs" {
        let current = tryProperty(system, kAudioHardwarePropertyDefaultOutputDevice,
                                  AudioDeviceID(kAudioObjectUnknown)) ?? AudioDeviceID(kAudioObjectUnknown)
        let rows: [[String: Any]] = allDevices()
            .filter { hasOutput($0) && !deviceName($0).isEmpty && !deviceUID($0).isEmpty }
            .map { ["uid": deviceUID($0), "name": deviceName($0), "kind": deviceKind($0),
                    "current": $0 == current] }
        let data = try! JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys])
        print(String(data: data, encoding: .utf8)!)
        exit(0)
    }
    guard args.count >= 3 else { fail("--set-output needs a device UID") }
    guard var id = allDevices().first(where: { hasOutput($0) && deviceUID($0) == args[2] }) else {
        fail("no output device with UID \(args[2])")
    }
    check(AudioObjectSetPropertyData(system, &address, 0, nil,
                                     UInt32(MemoryLayout<AudioDeviceID>.size), &id),
          "setting the default output")
    exit(0)
}

// MARK: - Main

runDeviceCommand()
let options = parseOptions()
if !options.stream {
    try? FileManager.default.createDirectory(at: options.outDir, withIntermediateDirectories: true)
}
let micURL = options.outDir.appendingPathComponent("\(options.name).mic.wav")
let systemURL = options.outDir.appendingPathComponent("\(options.name).system.wav")
let started = clock.start

let mic = MicCapture()
let system = SystemCapture()
do {
    if options.system { try system.start(url: options.stream ? nil : systemURL) }
    if options.mic { try mic.start(url: micURL) }
} catch {
    system.stop()
    fail("could not start: \(error)")
}
log("parola-capture: recording (stop with Ctrl-C)")

func dbfs(_ peak: Float) -> Double { peak > 0 ? max(-90, 20 * log10(Double(peak))) : -90 }

let levelTimer = DispatchSource.makeTimerSource(queue: .main)
if options.levels {
    levelTimer.schedule(deadline: .now() + 0.25, repeating: 0.25)
    levelTimer.setEventHandler {
        var parts: [String] = []
        if let t = mic.track { parts.append("\"mic\": \(String(format: "%.1f", dbfs(t.peak)))"); t.peak = 0 }
        if let t = system.track {
            parts.append("\"system\": \(String(format: "%.1f", dbfs(t.peak)))"); t.peak = 0
        }
        log("LEVELS {" + parts.joined(separator: ", ") + "}")
    }
    levelTimer.resume()
}

func finish() -> Never {
    if options.mic { mic.stop() }
    if options.system { system.stop() }
    if options.stream { exit(0) }  // nothing on disk, nothing to sum up
    // A side whose device was changing when the call ended: silence up to the end, so both
    // files have the same length.
    let end = clock.now
    if !events.isEmpty {
        if options.mic { mic.queue.sync { mic.track?.pad(to: end) } }
        if options.system { system.queue.sync { system.track?.pad(to: end) } }
    }
    var meta: [String: Any] = [
        "started_at": ISO8601DateFormatter().string(from: started),
        "elapsed_seconds": Date().timeIntervalSince(started),
    ]
    if let t = mic.track {
        meta["mic"] = ["file": micURL.lastPathComponent, "sample_rate": t.format.sampleRate,
                       "channels": t.format.channelCount, "seconds": t.seconds,
                       "input_device": mic.inputName]
    }
    if let t = system.track {
        meta["system"] = ["file": systemURL.lastPathComponent, "sample_rate": t.format.sampleRate,
                          "channels": t.format.channelCount, "seconds": t.seconds,
                          "output_device": system.outputName]
    }
    if options.mic { mic.queue.sync { mic.track?.close() } }
    if options.system { system.queue.sync { system.track?.close() } }
    if !events.isEmpty { meta["events"] = events }
    let data = try! JSONSerialization.data(withJSONObject: meta, options: [.prettyPrinted, .sortedKeys])
    try! data.write(to: options.outDir.appendingPathComponent("\(options.name).capture.json"))
    print(String(data: data, encoding: .utf8)!)
    exit(0)
}

signal(SIGINT, SIG_IGN)
signal(SIGTERM, SIG_IGN)
let sources = [SIGINT, SIGTERM].map { sig -> DispatchSourceSignal in
    let s = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    s.setEventHandler { finish() }
    s.resume()
    return s
}
let pauseSources = [(SIGUSR1, true), (SIGUSR2, false)].map { (sig, value) -> DispatchSourceSignal in
    let s = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    s.setEventHandler {
        pauseLock.lock(); pausedFlag = value; pauseLock.unlock()
        if value { clock.pause() } else { clock.resume() }
        log("parola-capture: " + (value ? "paused" : "resumed"))
    }
    s.resume()
    return s
}
_ = pauseSources
if let duration = options.duration {
    DispatchQueue.main.asyncAfter(deadline: .now() + duration) { finish() }
}
_ = sources
dispatchMain()
