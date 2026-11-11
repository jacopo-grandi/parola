// P.A.R.O.L.A.: an icon in the menu bar and a window, around the Python engine (decision 0028).

import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    /// SwiftUI puts its own object in NSApp.delegate: views reach this one through here.
    static weak var shared: AppDelegate?
    var engine: Engine?
    var recorder: FloatingRecorder?
    /// Opens the main window again once it was closed (SwiftUI's openWindow, kept by the views).
    var openMain: (() -> Void)?

    override init() {
        super.init()
        AppDelegate.shared = self
    }

    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { engine?.stop() }  // never leave the engine (or a capture) running
    }

    /// A click on the Dock icon, or opening the app again from Finder.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        MainActor.assumeIsolated { showMainWindow() }
        return false  // handled here
    }

    /// The main window in front, whatever happened to it: minimised to the Dock, hidden behind
    /// other apps, or closed. openWindow alone does nothing for a minimised window, which SwiftUI
    /// still counts as open.
    @MainActor func showMainWindow() {
        let window = NSApp.windows.first {
            !($0 is NSPanel) && ($0.identifier?.rawValue.hasPrefix("main") ?? false)
        }
        // Activation first: called from the floater the app is inactive, and without being
        // active makeKeyAndOrderFront raises the window only among our own.
        NSApp.activate(ignoringOtherApps: true)
        if let window, window.isMiniaturized {
            window.deminiaturize(nil)
            window.makeKeyAndOrderFront(nil)
        } else if let window, window.isVisible {
            window.makeKeyAndOrderFront(nil)
        } else {
            openMain?()
        }
        // If the system declined to activate us, at least the window comes visually forward.
        window?.orderFrontRegardless()
    }
}

@main
struct PAROLAApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var engine = Engine()
    // Noticing calls (decision 0056): who holds the microphone, and what to ask about it.
    @StateObject private var detector = CallDetector()
    @StateObject private var offer = CallOffer()

    var body: some Scene {
        Window("P.A.R.O.L.A.", id: "main") {
            MainView()
                .environmentObject(engine)
                .frame(minWidth: 820, minHeight: 520)
                .onAppear {
                    delegate.engine = engine
                    if delegate.recorder == nil {
                        delegate.recorder = FloatingRecorder(engine: engine, offer: offer)
                    }
                    offer.attach(engine: engine, detector: detector)
                    engine.start()
                }
        }
        .defaultSize(width: 1200, height: 800)

        MenuBarExtra {
            MenuContent()
                .environmentObject(engine)
                .environmentObject(detector)
                .environmentObject(offer)
        } label: {
            Image(systemName: engine.recordingState == "recording" ? "record.circle.fill"
                  : engine.listening ? "captions.bubble.fill" : "waveform")
        }
    }
}

struct MainView: View {
    @EnvironmentObject var engine: Engine
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        content.onAppear {
            // The window's own openWindow, for the Dock icon and the menu bar once it is closed.
            AppDelegate.shared?.openMain = { [openWindow] in openWindow(id: "main") }
        }
    }

    @ViewBuilder private var content: some View {
        switch engine.state {
        case .starting:
            VStack(spacing: 10) {
                ProgressView()
                Text("Avvio di P.A.R.O.L.A.…").foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color.black)
        case .ready(let url):
            WebView(url: url).background(Color.black)
        case .failed(let message):
            VStack(spacing: 10) {
                Text("P.A.R.O.L.A. non è partita").font(.headline)
                Text(message).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            .padding(40)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color.black)
        }
    }
}

struct MenuContent: View {
    @EnvironmentObject var engine: Engine
    @EnvironmentObject var detector: CallDetector
    @EnvironmentObject var offer: CallOffer
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        switch engine.recordingState {
        case "recording":
            Text("● In registrazione")
            Button("Ferma e trascrivi") { engine.record("stop") }
        case "transcribing":
            Text("Trascrizione in corso…")
        default:
            if engine.modelsReady {
                if engine.listening { Text("● Sottotitoli attivi") }
                // During a call, the call by name (decision 0056): "Registra la call su Zoom".
                if detector.call != nil, let call = offer.currentCall {
                    Button("Registra la c" + call.title.dropFirst()) { offer.recordCurrentCall() }
                        .disabled(engine.url == nil)
                } else {
                    Button("Registra una call") { engine.record("start") }
                        .disabled(engine.url == nil)
                }
                // Subtitles without recording (decision 0042).
                if engine.subtitlesAvailable {
                    Button(engine.listening ? "Spegni i sottotitoli" : "Sottotitoli EN→IT") {
                        engine.listen(!engine.listening)
                    }
                    .disabled(engine.url == nil)
                }
            } else {
                // The first download is asked in the window, with its size and progress.
                Button("Scarica i modelli…") { open() }
            }
        }
        Divider()
        Toggle("Proponi di registrare le call", isOn: $offer.enabled)
        if !detector.ignored.isEmpty {
            Menu("Non chiedere per") {
                ForEach(detector.ignored.sorted { $0.value < $1.value }, id: \.key) { bundle, name in
                    Button("\(name): chiedi di nuovo") { detector.unignore(bundle) }
                }
            }
        }
        Divider()
        Button("Apri P.A.R.O.L.A.") { open() }
        Divider()
        // Visible versioning (decision 0045): which build this is, at a glance.
        Text(buildLine)
        Button("Esci") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }

    private var buildLine: String {
        let info = Bundle.main
        let commit = info.object(forInfoDictionaryKey: "PAROLABuildCommit") as? String ?? "?"
        let date = info.object(forInfoDictionaryKey: "PAROLABuildDate") as? String ?? "?"
        return "Build \(commit) · \(date)"
    }

    private func open() {
        if AppDelegate.shared?.openMain == nil {
            AppDelegate.shared?.openMain = { openWindow(id: "main") }
        }
        AppDelegate.shared?.showMainWindow()
    }
}
