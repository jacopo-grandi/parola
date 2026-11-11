// What to ask about a call, and when (decision 0056). The detector says which app holds the
// microphone, the engine says whether a recording runs; this decides between offering to
// record, asking whether the call is over, or saying nothing. It never records on its own:
// starting always takes a click, so the participants' consent stays a person's choice.

import Combine
import Foundation

/// What the floating panel asks: "Registrare?" when a call starts, "È finita?" when it ends.
struct CallPrompt: Equatable {
    enum Kind { case offer, ended }
    let kind: Kind
    let app: CallApp
    /// When the prompt goes by itself: the offer disappears, the ended call stops and transcribes.
    let until: Date
    let duration: TimeInterval
    /// "Registra" was clicked and the engine has not said "recording" yet.
    var starting = false
}

enum PromptAction {
    case record, dismiss, ignoreApp, stop, keepRecording
}

@MainActor
final class CallOffer: ObservableObject {
    @Published private(set) var prompt: CallPrompt?
    /// "Proponi di registrare le call" in the menu bar; on by default.
    @Published var enabled = UserDefaults.standard.object(forKey: "PAROLACallOffer") as? Bool ?? true {
        didSet {
            UserDefaults.standard.set(enabled, forKey: "PAROLACallOffer")
            evaluate()
        }
    }

    static let offerSeconds: TimeInterval = 20
    static let endedSeconds: TimeInterval = 60

    private weak var engine: Engine?
    private weak var detector: CallDetector?
    private var watch: [AnyCancellable] = []
    private var deadline: Timer?
    /// The call already asked about: one question per call, answered or not.
    private var askedSession: Int?
    /// The call the running recording belongs to; nil for a recording with no call app.
    private var recordingCall: CallApp?
    /// "Continua" was chosen: this recording is not asked about again.
    private var keepRecording = false

    func attach(engine: Engine, detector: CallDetector) {
        guard self.engine == nil else { return }
        self.engine = engine
        self.detector = detector
        watch = [
            engine.$recording.sink { [weak self] _ in Task { @MainActor in self?.evaluate() } },
            detector.$call.sink { [weak self] _ in Task { @MainActor in self?.evaluate() } },
        ]
        detector.start()
    }

    /// The call to name in the menu bar ("Registra la call su Zoom"), when one is going on.
    var currentCall: CallApp? { enabled ? detector?.call : nil }

    func perform(_ action: PromptAction) {
        guard let prompt else { return }
        switch action {
        case .record:
            guard prompt.kind == .offer, !prompt.starting else { return }
            self.prompt?.starting = true
            deadline?.invalidate()
            engine?.record("start")
            // If the engine never says "recording" (a capture that fails), let the panel go.
            deadline = Timer.scheduledTimer(withTimeInterval: 8, repeats: false) { [weak self] _ in
                Task { @MainActor in
                    guard let self, self.prompt?.starting == true else { return }
                    self.prompt = nil
                }
            }
        case .dismiss:
            close()
        case .ignoreApp:
            detector?.ignore(prompt.app)
            close()
        case .stop:
            engine?.record("stop")
            close()
        case .keepRecording:
            keepRecording = true
            close()
        }
    }

    /// From the menu bar: record the call going on, which then counts as this recording's call.
    func recordCurrentCall() {
        if let call = currentCall { askedSession = call.session }
        close()
        engine?.record("start")
    }

    private func close() {
        deadline?.invalidate()
        deadline = nil
        prompt = nil
    }

    private func show(_ kind: CallPrompt.Kind, _ app: CallApp) {
        let seconds = kind == .offer ? Self.offerSeconds : Self.endedSeconds
        prompt = CallPrompt(kind: kind, app: app, until: Date().addingTimeInterval(seconds), duration: seconds)
        deadline?.invalidate()
        deadline = Timer.scheduledTimer(withTimeInterval: seconds, repeats: false) { [weak self] _ in
            Task { @MainActor in self?.expire(kind) }
        }
    }

    private func expire(_ kind: CallPrompt.Kind) {
        guard prompt?.kind == kind, prompt?.starting != true else { return }
        if kind == .ended { engine?.record("stop") }  // nobody answered: the call is over
        close()
    }

    private func evaluate() {
        guard let engine else { return }
        let state = engine.recordingState
        let call = detector?.call
        guard enabled else {
            if prompt != nil { close() }
            recordingCall = nil
            return
        }
        if state == "recording" {
            if prompt?.kind == .offer { close() }  // started, from the panel or elsewhere
            if recordingCall == nil, let call {
                recordingCall = call
                askedSession = call.session
            }
            guard let attached = recordingCall else { return }  // started by hand, no call app
            if let call, call.pid == attached.pid {
                // The app took the microphone back (or never really let go): still the call.
                recordingCall = call
                if prompt?.kind == .ended { close() }
            } else if prompt == nil && !keepRecording {
                show(.ended, attached)
            }
            return
        }
        // Not recording: idle, done or error (the last call stays "done"), or transcribing.
        recordingCall = nil
        keepRecording = false
        if prompt?.kind == .ended { close() }
        if prompt?.starting == true { return }  // waiting for the engine to say "recording"
        if let prompt, prompt.kind == .offer, prompt.app != call { close() }  // the call ended
        guard let call, prompt == nil, askedSession != call.session,
              state != "transcribing", engine.modelsReady, engine.url != nil
        else { return }
        askedSession = call.session
        show(.offer, call)
    }
}
