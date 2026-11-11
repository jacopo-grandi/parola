// The floating recorder (phase 8, step 2): a small panel above every other window, Meet
// included, that can shrink to a blinking dot and be dragged anywhere. It shows up when a
// recording starts and goes away when it stops. When a call starts it first asks whether to
// record it, and when the call ends it asks whether to stop (decision 0056): the same panel,
// so "Registra" turns the question into the recorder where it is.

import AppKit
import Combine
import SwiftUI

/// A borderless panel that never takes the focus away from the call.
final class RecorderPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// Dragging the panel by any spot that is not a button.
final class DraggableHostingView<Content: View>: NSHostingView<Content> {
    override var mouseDownCanMoveWindow: Bool { true }
}

/// A view under the panel's content that moves the window while the mouse is dragged, since
/// a borderless panel whose content is SwiftUI does not move by itself. A press that barely
/// moves is a click: ``onClick`` (the dot opens on a click); the second of a double click is
/// ``onDoubleClick`` (the main window, from the dot or the panel).
struct DragArea: NSViewRepresentable {
    var onClick: (() -> Void)?
    var onDoubleClick: (() -> Void)?

    func makeNSView(context: Context) -> DragView {
        let view = DragView()
        view.onClick = onClick
        view.onDoubleClick = onDoubleClick
        return view
    }

    func updateNSView(_ view: DragView, context: Context) {
        view.onClick = onClick
        view.onDoubleClick = onDoubleClick
    }

    final class DragView: NSView {
        var onClick: (() -> Void)?
        var onDoubleClick: (() -> Void)?

        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

        override func mouseDown(with event: NSEvent) {
            guard let window else { return }
            let start = NSEvent.mouseLocation
            let origin = window.frame.origin
            var moved = false
            // Follow the mouse until the button is released.
            while let next = window.nextEvent(matching: [.leftMouseDragged, .leftMouseUp]) {
                if next.type == .leftMouseUp { break }
                let now = NSEvent.mouseLocation
                let dx = now.x - start.x, dy = now.y - start.y
                if !moved && hypot(dx, dy) < 3 { continue }
                moved = true
                window.setFrameOrigin(NSPoint(x: origin.x + dx, y: origin.y + dy))
            }
            if moved {
                window.saveFrame(usingName: "PAROLARecorder")
            } else if event.clickCount >= 2, let onDoubleClick {
                onDoubleClick()
            } else {
                onClick?()
            }
        }
    }
}

@MainActor
final class FloatingRecorder {
    private let engine: Engine
    private let model = RecorderModel()
    private var panel: RecorderPanel?
    private var watch: AnyCancellable?

    static let full = NSSize(width: 252, height: 48)
    static let icon = NSSize(width: 40, height: 40)
    /// The call question: room for the app's name and two buttons.
    static let prompt = NSSize(width: 372, height: 48)

    private var watchPrompt: AnyCancellable?
    private var recording = false

    init(engine: Engine, offer: CallOffer) {
        self.engine = engine
        model.onAction = { [weak engine] action in engine?.record(action) }
        model.onResize = { [weak self] _ in self?.applySize() }
        model.onPrompt = { [weak offer] action in offer?.perform(action) }
        watch = engine.$recording.sink { [weak self] status in
            Task { @MainActor in self?.update(status) }
        }
        watchPrompt = offer.$prompt.sink { [weak self] prompt in
            Task { @MainActor in
                guard let self else { return }
                let changed = (prompt == nil) != (self.model.prompt == nil)
                self.model.prompt = prompt
                if changed { self.applySize() }
                self.updateVisibility()
            }
        }
    }

    private func update(_ status: [String: Any]) {
        recording = (status["state"] as? String) == "recording"
        model.apply(status)
        updateVisibility()
    }

    private func updateVisibility() {
        if recording || model.prompt != nil { show() } else { hide() }
    }

    /// The panel's size for what it shows now: a question, the bar, or the dot.
    private func panelSize() -> NSSize {
        if model.prompt != nil { return Self.prompt }
        return model.compact ? Self.icon : Self.full
    }

    private func makePanel() -> RecorderPanel {
        let size = panelSize()
        let panel = RecorderPanel(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panel.isMovableByWindowBackground = true
        panel.hidesOnDeactivate = false
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = true
        panel.contentView = DraggableHostingView(rootView: RecorderView(model: model))
        // Where the user left it last time; the first time, top right of the screen.
        if !panel.setFrameUsingName("PAROLARecorder"), let screen = NSScreen.main?.visibleFrame {
            panel.setFrameOrigin(NSPoint(x: screen.maxX - size.width - 24, y: screen.maxY - size.height - 24))
        }
        panel.setFrameAutosaveName("PAROLARecorder")
        // The saved frame has the size of its time (the dot, the bar): fit it to now.
        fit(panel, to: size, animate: false)
        return panel
    }

    private func show() {
        if panel == nil { panel = makePanel() }
        panel?.orderFrontRegardless()
    }

    private func hide() {
        panel?.orderOut(nil)
    }

    /// Shrink to the dot, grow back, or make room for a question, keeping the top corner on
    /// the side of the nearer screen edge where it is: a panel at the right edge grows leftward
    /// and never runs off the screen.
    private func applySize() {
        guard let panel else { return }
        fit(panel, to: panelSize(), animate: true)
    }

    private func fit(_ panel: NSPanel, to size: NSSize, animate: Bool) {
        var frame = panel.frame
        guard frame.size != size else { return }
        let screen = (panel.screen ?? NSScreen.main)?.visibleFrame ?? frame
        frame.origin.y += frame.height - size.height
        if frame.midX > screen.midX { frame.origin.x += frame.width - size.width }
        frame.size = size
        panel.setFrame(frame, display: true, animate: animate)
    }
}

@MainActor
final class RecorderModel: ObservableObject {
    @Published var elapsed: Double = 0
    @Published var paused = false
    @Published var levels: (mic: Double?, system: Double?) = (nil, nil)
    @Published var compact = UserDefaults.standard.bool(forKey: "PAROLARecorderCompact")
    // Toggled once when the panel appears; the animation repeats it forever. Kept here and not
    // in a SwiftUI @State, which needs Xcode's macro plugins to compile.
    @Published var blink = false
    /// The call question shown instead of the recorder (decision 0056), when there is one.
    @Published var prompt: CallPrompt?
    private var fetchedAt = Date()
    var onAction: (String) -> Void = { _ in }
    var onResize: (Bool) -> Void = { _ in }
    var onPrompt: (PromptAction) -> Void = { _ in }

    func apply(_ status: [String: Any]) {
        elapsed = status["elapsed"] as? Double ?? 0
        paused = status["paused"] as? Bool ?? false
        let lv = status["levels"] as? [String: Double] ?? [:]
        levels = (lv["mic"], lv["system"])
        fetchedAt = Date()
    }

    /// The clock keeps running between two status updates of the engine.
    func time(at now: Date) -> String {
        let seconds = Int(elapsed + (paused ? 0 : now.timeIntervalSince(fetchedAt)))
        let h = seconds / 3600, m = (seconds % 3600) / 60, s = seconds % 60
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, s) : String(format: "%02d:%02d", m, s)
    }

    func toggleSize() {
        compact.toggle()
        UserDefaults.standard.set(compact, forKey: "PAROLARecorderCompact")
        onResize(compact)
    }
}

struct RecorderView: View {
    @ObservedObject var model: RecorderModel
    private var blink: Bool { model.blink }

    var body: some View {
        Group {
            if let prompt = model.prompt { CallPromptBar(prompt: prompt, act: model.onPrompt) }
            else if model.compact { icon } else { full }
        }
        .onAppear { model.blink = true }
    }

    private var dot: some View {
        Circle()
            .fill(model.paused ? Color.gray : Color.red)
            .frame(width: 11, height: 11)
            .opacity(model.paused ? 0.6 : (blink ? 0.25 : 1))
            .animation(model.paused ? .default : .easeInOut(duration: 0.6).repeatForever(), value: blink)
    }

    private var icon: some View {
        ZStack {
            Circle().fill(Color.black.opacity(0.88))
            Circle().strokeBorder(Color.white.opacity(0.12))
            dot.allowsHitTesting(false)
            // On top: a click opens the panel, a double click the app, a drag moves the dot.
            DragArea(onClick: model.toggleSize, onDoubleClick: { AppDelegate.shared?.showMainWindow() })
                .clipShape(Circle())
        }
        .help("In registrazione: clic per aprire il pannello, doppio clic per P.A.R.O.L.A., trascina per spostare")
        .frame(width: FloatingRecorder.icon.width, height: FloatingRecorder.icon.height)
    }

    private var full: some View {
        HStack(spacing: 10) {
            Group {
                dot
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Text(model.time(at: context.date))
                        .font(.system(size: 14, weight: .semibold).monospacedDigit())
                        .foregroundStyle(.white)
                        .lineLimit(1)
                        .fixedSize()  // "12:34" never wraps; the meters give way instead
                }
                VStack(spacing: 3) {
                    meter(model.levels.mic)
                    meter(model.levels.system)
                }
                .frame(width: 44)
                Spacer(minLength: 0)
            }
            .allowsHitTesting(false)  // clicks here fall through to the drag area
            control(model.paused ? "play.fill" : "pause.fill", model.paused ? "Riprendi" : "Pausa") {
                model.onAction(model.paused ? "resume" : "pause")
            }
            control("stop.fill", "Ferma e trascrivi") { model.onAction("stop") }
            control("minus", "Riduci a icona") { model.toggleSize() }
        }
        .padding(.horizontal, 14)
        .frame(width: FloatingRecorder.full.width, height: FloatingRecorder.full.height)
        // Anywhere but the buttons drags the panel; a double click brings the app in front.
        .background(DragArea(onClick: nil, onDoubleClick: { AppDelegate.shared?.showMainWindow() }))
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.black.opacity(0.88)))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Color.white.opacity(0.12)))
    }

    /// -60 dBFS (silence) .. 0 dBFS (full scale).
    private func meter(_ db: Double?) -> some View {
        let value = db.map { max(0, min(1, ($0 + 60) / 60)) } ?? 0
        return GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.white.opacity(0.12))
                Capsule().fill(Color.white.opacity(0.7)).frame(width: geo.size.width * value)
            }
        }
        .frame(height: 3)
    }

    private func control(_ symbol: String, _ label: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.white.opacity(0.85))
                .frame(width: 26, height: 26)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(label)
        .accessibilityLabel(label)
    }
}

/// The call question (decision 0056), in the recorder's look: "Call su Zoom · Registrare con
/// P.A.R.O.L.A.?" when a call starts, "La call è finita?" when its app lets the microphone go.
/// A thin bar along the bottom shows the time left before it goes by itself.
struct CallPromptBar: View {
    let prompt: CallPrompt
    let act: (PromptAction) -> Void

    var body: some View {
        HStack(spacing: 10) {
            Group {
                leading
                VStack(alignment: .leading, spacing: 1) {
                    Text(title)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white)
                    subtitle
                        .font(.system(size: 12))
                        .foregroundStyle(.white.opacity(0.6))
                }
                .lineLimit(1)
                .truncationMode(.middle)
                Spacer(minLength: 0)
            }
            .allowsHitTesting(false)  // clicks here fall through to the drag area
            if prompt.kind == .offer { offerButtons } else { endedButtons }
        }
        .padding(.horizontal, 14)
        .frame(width: FloatingRecorder.prompt.width, height: FloatingRecorder.prompt.height)
        .background(DragArea(onClick: nil, onDoubleClick: { AppDelegate.shared?.showMainWindow() }))
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.black.opacity(0.88)))
        .overlay(alignment: .bottom) { countdown }
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Color.white.opacity(0.12)))
    }

    private var title: String {
        prompt.kind == .offer ? prompt.app.title : "La call è finita?"
    }

    @ViewBuilder private var subtitle: some View {
        if prompt.kind == .offer {
            Text(prompt.starting ? "Avvio della registrazione…" : "Registrare con P.A.R.O.L.A.?")
        } else {
            TimelineView(.periodic(from: .now, by: 1)) { context in
                let left = max(0, Int(prompt.until.timeIntervalSince(context.date).rounded(.up)))
                Text(String(format: "Si ferma tra %d:%02d", left / 60, left % 60))
                    .monospacedDigit()
            }
        }
    }

    /// The call's app icon when offering; the grey dot of a recording about to stop.
    @ViewBuilder private var leading: some View {
        if prompt.kind == .offer, let icon = prompt.app.icon {
            Image(nsImage: icon).resizable().frame(width: 22, height: 22)
        } else {
            Circle().fill(prompt.kind == .offer ? Color.red : Color.gray)
                .frame(width: 11, height: 11)
                .padding(.horizontal, 5)
        }
    }

    private var offerButtons: some View {
        HStack(spacing: 2) {
            pill("Registra", primary: true) { act(.record) }
                .disabled(prompt.starting)
            Menu {
                Button("Non chiedere per \(prompt.app.name)") { act(.ignoreApp) }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.85))
                    .frame(width: 26, height: 26)
                    .contentShape(Rectangle())
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .frame(width: 26)
            .help("Altre scelte")
            .accessibilityLabel("Altre scelte")
            Button(action: { act(.dismiss) }) {
                Image(systemName: "xmark")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.85))
                    .frame(width: 26, height: 26)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("Non ora")
            .accessibilityLabel("Non ora")
        }
    }

    private var endedButtons: some View {
        HStack(spacing: 4) {
            pill("Ferma e trascrivi", primary: true) { act(.stop) }
            pill("Continua", primary: false) { act(.keepRecording) }
        }
    }

    private func pill(_ label: String, primary: Bool, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(primary ? Color.black : Color.white.opacity(0.85))
                .lineLimit(1)
                .fixedSize()
                .padding(.horizontal, primary ? 10 : 6)
                .frame(height: 26)
                .background(RoundedRectangle(cornerRadius: 7, style: .continuous)
                    .fill(primary ? Color(white: 0.96) : Color.clear))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    /// The time left, as a line that shrinks along the bottom edge.
    private var countdown: some View {
        TimelineView(.periodic(from: .now, by: 0.25)) { context in
            let left = prompt.starting ? 1 : max(0, min(1, prompt.until.timeIntervalSince(context.date) / prompt.duration))
            GeometryReader { geo in
                Rectangle().fill(Color.white.opacity(0.35))
                    .frame(width: geo.size.width * left, height: 2)
                    .animation(.linear(duration: 0.25), value: left)
            }
            .frame(height: 2)
        }
        .allowsHitTesting(false)
    }
}
