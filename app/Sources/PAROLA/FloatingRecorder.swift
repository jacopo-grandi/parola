// The floating recorder (phase 8, step 2): a small panel above every other window, Meet
// included, that can shrink to a blinking dot and be dragged anywhere. It shows up when a
// recording starts and goes away when it stops. While listening for subtitles without a
// recording (decision 0042) it shows too, with a still dot that starts a recording. When a
// call starts it first asks whether to record it, and when the call ends it asks whether to
// stop (decision 0056): the same panel, so "Registra" turns the question into the recorder.

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
    /// The bar must grow with the CC toggle, or the dot and the minus touch the borders.
    static func barWidth(cc: Bool) -> CGFloat { cc ? 290 : full.width }
    // The subtitle strip under the panel (ToTTI, decision 0038): wide enough to read a line,
    // so the panel can sit at the screen edge where subtitles normally live.
    static let stripWidth: CGFloat = 480
    // Room for the previous line plus two live lines at 16 pt: at 66 the bottom-aligned
    // stack clipped the previous line whenever the live text wrapped.
    static let stripHeight: CGFloat = 78
    /// The call question: room for the app's name and two buttons.
    static let prompt = NSSize(width: 372, height: 48)

    private var watchSubs: [AnyCancellable] = []
    private var watchPrompt: AnyCancellable?
    private var active = false

    init(engine: Engine, offer: CallOffer) {
        self.engine = engine
        model.onAction = { [weak engine] action in engine?.record(action) }
        model.onResize = { [weak self] _ in self?.applySize() }
        model.onPrompt = { [weak offer] action in offer?.perform(action) }
        watchPrompt = offer.$prompt.sink { [weak self] prompt in
            Task { @MainActor in
                guard let self else { return }
                let changed = (prompt == nil) != (self.model.prompt == nil)
                self.model.prompt = prompt
                if changed { self.applySize() }
                self.updateVisibility()
            }
        }
        model.onSubtitles = { [weak engine] on in engine?.setSubtitles(on) }
        model.onSubsLang = { [weak engine] lang in engine?.setSubtitlesLang(lang) }
        model.onListen = { [weak engine] on in engine?.listen(on) }
        watch = engine.$recording.sink { [weak self] status in
            Task { @MainActor in self?.update(status) }
        }
        watchSubs = [
            engine.$subtitlesOn.sink { [weak self] on in
                Task { @MainActor in
                    self?.model.subsOn = on
                    self?.applySize()
                }
            },
            engine.$subtitlesAvailable.sink { [weak self] available in
                Task { @MainActor in
                    self?.model.subsAvailable = available
                    self?.applySize()  // the CC toggle widens the bar
                }
            },
            engine.$subtitles.sink { [weak self] status in
                Task { @MainActor in self?.model.applySubtitles(status) }
            },
        ]
    }

    private func update(_ status: [String: Any]) {
        let recording = (status["state"] as? String) == "recording"
        let listening = status["listening"] as? Bool ?? false
        let changed = listening != model.listening
        model.apply(status)
        if changed { applySize() }  // the listening bar has no CC toggle: narrower
        active = recording || listening
        updateVisibility()
    }

    private func updateVisibility() {
        if active || model.prompt != nil { show() } else { hide() }
    }

    /// The panel's size for the current form: the bar or the dot, plus the strip when on.
    private func panelSize() -> NSSize {
        let base = model.prompt != nil ? Self.prompt : model.compact
            ? Self.icon
            : NSSize(width: Self.barWidth(cc: model.subsAvailable && !model.listening),
                     height: Self.full.height)
        guard model.subsOn else { return base }
        return NSSize(width: Self.stripWidth, height: base.height + 6 + Self.stripHeight)
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

    /// Shrink to the dot, grow back, make room for a question or for the subtitle strip,
    /// keeping the top corner on the side of the nearer screen edge where it is: the strip grows
    /// downward from the bar, and a panel at the right edge grows leftward, never off the screen.
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
    /// Subtitles without a recording (decision 0042): still dot, no timer, no pause or stop.
    @Published var listening = false
    @Published var levels: (mic: Double?, system: Double?) = (nil, nil)
    @Published var compact = UserDefaults.standard.bool(forKey: "PAROLARecorderCompact")
    // Live subtitles (ToTTI, decision 0038): the toggle's state, whether the translation
    // model is on disk, and the last two Italian lines for the strip.
    @Published var subsOn = false
    @Published var subsAvailable = false
    @Published var subsNow = ""
    @Published var subsPrev = ""
    // "it": translated, committed lines only. "en": the original, whose last line carries
    // the still-provisional tail, dimmed (text at decode latency, refined afterwards).
    @Published var subsLang = "it"
    @Published var subsTail = ""
    /// The call question shown instead of the recorder (decision 0056), when there is one.
    @Published var prompt: CallPrompt?
    var onPrompt: (PromptAction) -> Void = { _ in }
    private var fetchedAt = Date()
    var onAction: (String) -> Void = { _ in }
    var onResize: (Bool) -> Void = { _ in }
    var onSubtitles: (Bool) -> Void = { _ in }
    var onSubsLang: (String) -> Void = { _ in }
    var onListen: (Bool) -> Void = { _ in }

    func apply(_ status: [String: Any]) {
        elapsed = status["elapsed"] as? Double ?? 0
        paused = status["paused"] as? Bool ?? false
        listening = (status["state"] as? String) != "recording" && (status["listening"] as? Bool ?? false)
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

    func toggleSubs() {
        onSubtitles(!subsOn)
    }

    func toggleSubsLang() {
        onSubsLang(subsLang == "it" ? "en" : "it")
    }

    func applySubtitles(_ status: [String: Any]) {
        if let lang = status["lang"] as? String { subsLang = lang }
        let lines = status["lines"] as? [[String: Any]] ?? []
        let key = subsLang == "en" ? "en" : "it"
        let shown = lines.compactMap { $0[key] as? String }.filter { !$0.isEmpty }
        subsNow = shown.last ?? ""
        subsPrev = shown.count > 1 ? shown[shown.count - 2] : ""
        subsTail = subsLang == "en" ? (status["tail"] as? String ?? "") : ""
    }
}

struct RecorderView: View {
    @ObservedObject var model: RecorderModel

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Group {
                if let prompt = model.prompt { CallPromptBar(prompt: prompt, act: model.onPrompt) }
                else if model.compact { icon } else if model.listening { listenBar } else { full }
            }
            // The subtitle strip hangs under the bar (and under the dot too), so the panel
            // can sit at the screen edge where subtitles normally live (decision 0038).
            if model.subsOn { strip }
        }
    }

    private var strip: some View {
        ZStack(alignment: .topTrailing) {
            VStack(alignment: .leading, spacing: 2) {
                if !model.subsPrev.isEmpty {
                    Text(model.subsPrev)
                        .font(.system(size: 13))
                        .foregroundStyle(.white.opacity(0.55))
                        .lineLimit(1)
                }
                liveLine
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            // The language of the strip: Italian translated, or the English original.
            Button(model.subsLang == "en" ? "EN" : "IT") { model.toggleSubsLang() }
                .buttonStyle(.plain)
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.white.opacity(0.65))
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(RoundedRectangle(cornerRadius: 5).fill(Color.white.opacity(0.14)))
                .padding(6)
                .help(model.subsLang == "en"
                      ? "Inglese originale · clic per l'italiano tradotto"
                      : "Italiano tradotto · clic per l'inglese originale")
        }
        .frame(width: FloatingRecorder.stripWidth, height: FloatingRecorder.stripHeight)
        .background(DragArea(onClick: nil, onDoubleClick: { AppDelegate.shared?.showMainWindow() }))
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.black.opacity(0.88)))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Color.white.opacity(0.12)))
    }

    /// The live line: committed text plain and, in the EN view, the provisional tail dimmed.
    /// Head truncation, like broadcast subtitles: when the line overflows, the START is cut,
    /// never the end — the end is where the new words (and the whole EN tail) appear, and
    /// with the default tail truncation they were invisible until a sentence closed, which
    /// read as "the subtitles move one sentence at a time".
    private var liveLine: some View {
        let empty = model.subsNow.isEmpty && model.subsTail.isEmpty
        let joint = model.subsNow.isEmpty || model.subsTail.isEmpty ? "" : " "
        return (Text(empty ? "Sottotitoli in arrivo…" : model.subsNow)
                    .foregroundColor(empty ? .white.opacity(0.5) : .white)
                + Text(joint + model.subsTail)
                    .foregroundColor(.white.opacity(0.45)))
            .font(.system(size: 16, weight: .medium))
            .lineLimit(2)
            .truncationMode(.head)
    }

    /// Blinks while recording; still while paused (grey) or only listening (decision 0042).
    /// The phase comes from the clock, so the blinking starts again by itself when listening
    /// turns into a recording.
    private var dot: some View {
        TimelineView(.periodic(from: .now, by: 0.6)) { context in
            let dim = Int(context.date.timeIntervalSinceReferenceDate / 0.6) % 2 == 0
            Circle()
                .fill(model.paused ? Color.gray : Color.red)
                .frame(width: 11, height: 11)
                .opacity(model.paused ? 0.6 : (!model.listening && dim ? 0.25 : 1))
                .animation(.easeInOut(duration: 0.6), value: dim)
        }
        .frame(width: 11, height: 11)
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
        .help(model.listening
            ? "Sottotitoli: clic per aprire il pannello, doppio clic per P.A.R.O.L.A., trascina per spostare"
            : "In registrazione: clic per aprire il pannello, doppio clic per P.A.R.O.L.A., trascina per spostare")
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
            if model.subsAvailable {
                control("captions.bubble", "Sottotitoli live", active: model.subsOn) {
                    model.toggleSubs()
                }
            }
            control("stop.fill", "Ferma e trascrivi") { model.onAction("stop") }
            control("minus", "Riduci a icona") { model.toggleSize() }
        }
        .padding(.horizontal, 14)
        .frame(width: FloatingRecorder.barWidth(cc: model.subsAvailable),
               height: FloatingRecorder.full.height)
        // Anywhere but the buttons drags the panel; a double click brings the app in front.
        .background(DragArea(onClick: nil, onDoubleClick: { AppDelegate.shared?.showMainWindow() }))
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.black.opacity(0.88)))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Color.white.opacity(0.12)))
    }

    /// Listening without recording (decision 0042): the still dot starts a recording, × switches
    /// the subtitles off. No timer, no pause, no stop: nothing is being recorded.
    private var listenBar: some View {
        HStack(spacing: 10) {
            Button(action: { model.onAction("start") }) {
                dot.frame(width: 26, height: 26).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(.leading, -8)  // the dot sits where it does in the recording bar
            .help("Registra la call da adesso")
            .accessibilityLabel("Registra")
            Group {
                Text("Sottotitoli")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                    .fixedSize()
                meter(model.levels.system).frame(width: 44)
                Spacer(minLength: 0)
            }
            .padding(.leading, -8)
            .allowsHitTesting(false)  // clicks here fall through to the drag area
            control("minus", "Riduci a icona") { model.toggleSize() }
            control("xmark", "Spegni i sottotitoli") { model.onListen(false) }
        }
        .padding(.horizontal, 14)
        .frame(width: FloatingRecorder.barWidth(cc: false), height: FloatingRecorder.full.height)
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

    private func control(_ symbol: String, _ label: String, active: Bool = false,
                         _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(active ? Color.accentColor : .white.opacity(0.85))
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
