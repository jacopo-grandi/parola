// Noticing a call (decision 0056): another app starts using the microphone, P.A.R.O.L.A. offers
// to record; the app lets go of it while recording, P.A.R.O.L.A. asks whether the call is over.
// The signal is Core Audio's list of processes using audio (macOS 14+), read once a second,
// without any permission. Muting does not release the microphone in Meet or Zoom, so a release
// means the call ended.

import AppKit
import Combine
import CoreAudio
import Foundation

/// The app holding the microphone, as the person knows it: Chrome, not its helper process.
struct CallApp: Equatable {
    /// A new value for every time an app takes the microphone: one question per call.
    let session: Int
    let pid: pid_t
    let bundleID: String
    let name: String
    let icon: NSImage?

    static func == (a: CallApp, b: CallApp) -> Bool { a.session == b.session }

    /// "Call su Zoom", but "Call in Chrome": in a browser the call is in a tab we cannot name
    /// (the tab title needs the Screen Recording permission, which the app does not ask for).
    var title: String {
        let browsers = ["com.google.chrome", "com.apple.safari", "company.thebrowser.browser",
                        "org.mozilla.firefox", "com.microsoft.edgemac", "com.brave.browser",
                        "com.vivaldi.vivaldi", "com.operasoftware.opera"]
        return browsers.contains(bundleID.lowercased()) ? "Call in \(name)" : "Call su \(name)"
    }
}

/// Which app is holding the microphone, published after it has held it for ``holdDelay``.
@MainActor
final class CallDetector: ObservableObject {
    @Published private(set) var call: CallApp?

    /// Short blips (a sound check, an app probing the device) do not make a call.
    static let holdDelay: TimeInterval = 3
    /// How long the call's app may be without the microphone before the call counts as over.
    static let releaseDelay: TimeInterval = 2

    /// Apps that use the microphone without being a call. System dictation and Siri run in
    /// background processes, which never count anyway (only apps with a Dock icon do).
    static let builtInExclusions: Set<String> = [
        "com.apple.voicememos", "com.apple.quicktimeplayerx", "com.apple.garageband10",
        "com.apple.logic10", "com.apple.mainstage3", "com.apple.photobooth",
        "com.apple.systempreferences", "com.apple.audio.audiomidisetup",
        "dev.parola.app", "dev.ilat.capture",
    ]

    /// Once a second: Core Audio announces new processes but, measured on macOS 27, not a
    /// process starting to use the microphone, so a listener alone missed calls (decision 0056).
    /// A read is about forty property lookups.
    private var poll: Timer?
    /// The app seen holding the microphone and since when, until it is confirmed as ``call``.
    private var candidate: (pid: pid_t, since: Date)?
    private var confirm: Timer?
    private var release: Timer?
    private var nextSession = 1
    private let ownPID = ProcessInfo.processInfo.processIdentifier

    /// The person's own exclusions ("Non chiedere per <app>"), by bundle ID.
    @Published private(set) var ignored: [String: String] =
        UserDefaults.standard.dictionary(forKey: "PAROLACallIgnored") as? [String: String] ?? [:]

    func start() {
        guard poll == nil else { return }
        poll = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.rescan() }
        }
        poll?.tolerance = 0.3
        rescan()
    }

    /// Never ask again for this app, until the person takes it off the list from the menu bar.
    func ignore(_ app: CallApp) {
        ignored[app.bundleID] = app.name
        UserDefaults.standard.set(ignored, forKey: "PAROLACallIgnored")
        if call?.bundleID == app.bundleID {
            release?.invalidate()
            release = nil
            call = nil
        }
    }

    func unignore(_ bundleID: String) {
        ignored.removeValue(forKey: bundleID)
        UserDefaults.standard.set(ignored, forKey: "PAROLACallIgnored")
        rescan()
    }

    // MARK: - Core Audio

    private static func address(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                   mElement: kAudioObjectPropertyElementMain)
    }

    private static func processObjects() -> [AudioObjectID] {
        var address = address(kAudioHardwarePropertyProcessObjectList)
        var size: UInt32 = 0
        let system = AudioObjectID(kAudioObjectSystemObject)
        guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &size) == noErr, size > 0 else { return [] }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(system, &address, 0, nil, &size, &ids) == noErr else { return [] }
        return ids
    }

    private static func uint32(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> UInt32? {
        var address = address(selector)
        var value: UInt32 = 0
        var size = UInt32(MemoryLayout<UInt32>.size)
        guard AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value) == noErr else { return nil }
        return value
    }

    /// The apps using the microphone now, each mapped to the app the person sees in the Dock.
    private func appsUsingMicrophone() -> [NSRunningApplication] {
        var seen = Set<pid_t>()
        var apps: [NSRunningApplication] = []
        for object in Self.processObjects() where Self.uint32(object, kAudioProcessPropertyIsRunningInput) ?? 0 != 0 {
            guard let raw = Self.uint32(object, kAudioProcessPropertyPID) else { continue }
            let pid = Responsibility.owner(of: pid_t(bitPattern: raw))
            guard pid != ownPID, !seen.contains(pid),
                  let app = NSRunningApplication(processIdentifier: pid),
                  app.activationPolicy == .regular,  // background services are never a call
                  let bundle = app.bundleIdentifier?.lowercased(),
                  !Self.builtInExclusions.contains(bundle),
                  !ignored.keys.contains(where: { $0.lowercased() == bundle })
            else { continue }
            seen.insert(pid)
            apps.append(app)
        }
        return apps.sorted { $0.processIdentifier < $1.processIdentifier }
    }

    /// The current call stays the call while its app holds the microphone, even if a second app
    /// takes it too; when it lets go, another app holding it becomes a new candidate.
    private func rescan() {
        let apps = appsUsingMicrophone()
        if let call {
            if apps.contains(where: { $0.processIdentifier == call.pid }) {
                release?.invalidate()
                release = nil
                return
            }
            // Switching the microphone in Meet releases it for an instant: the call ends only
            // if the app is still without it after a moment.
            if release == nil {
                release = Timer.scheduledTimer(withTimeInterval: Self.releaseDelay, repeats: false) { [weak self] _ in
                    Task { @MainActor in
                        self?.release = nil
                        self?.call = nil
                        self?.rescan()
                    }
                }
            }
            return
        }
        guard let first = apps.first else {
            candidate = nil
            confirm?.invalidate()
            return
        }
        let app = apps.first(where: { $0.processIdentifier == candidate?.pid }) ?? first
        if candidate?.pid == app.processIdentifier { return }  // already counting
        candidate = (app.processIdentifier, Date())
        confirm?.invalidate()
        confirm = Timer.scheduledTimer(withTimeInterval: Self.holdDelay, repeats: false) { [weak self] _ in
            Task { @MainActor in self?.confirmCandidate() }
        }
    }

    private func confirmCandidate() {
        guard let candidate,
              let app = appsUsingMicrophone().first(where: { $0.processIdentifier == candidate.pid })
        else { return }
        self.candidate = nil
        call = CallApp(session: nextSession, pid: app.processIdentifier,
                       bundleID: app.bundleIdentifier ?? "", name: app.localizedName ?? "un'app",
                       icon: app.icon)
        nextSession += 1
    }
}

/// The app a process works for: Chrome for "Google Chrome Helper", Safari for WebKit's audio
/// process. macOS keeps this as the process "responsible" for another (it is the app the
/// privacy prompts name); the call is looked up at run time, since the SDK does not declare it.
enum Responsibility {
    private typealias Lookup = @convention(c) (pid_t) -> pid_t
    private static let lookup: Lookup? = {
        guard let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2),  // RTLD_DEFAULT
                                 "responsibility_get_pid_responsible_for_pid") else { return nil }
        return unsafeBitCast(symbol, to: Lookup.self)
    }()

    static func owner(of pid: pid_t) -> pid_t {
        guard let lookup else { return pid }
        let owner = lookup(pid)
        return owner > 0 ? owner : pid
    }
}
