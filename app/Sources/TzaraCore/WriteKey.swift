import CoreGraphics
import Foundation

/// The modifier keys that matter to the hotkey.
public struct ModifierSet: OptionSet, Hashable, Sendable {
    public let rawValue: Int
    public init(rawValue: Int) { self.rawValue = rawValue }

    public static let control = ModifierSet(rawValue: 1 << 0)
    public static let option = ModifierSet(rawValue: 1 << 1)
    public static let command = ModifierSet(rawValue: 1 << 2)
    public static let shift = ModifierSet(rawValue: 1 << 3)
    public static let function = ModifierSet(rawValue: 1 << 4)

    /// Reads the modifier keys held down from a keyboard event's flags.
    ///
    /// Caps Lock is ignored: it stays "on" and says nothing about what is being pressed.
    public init(flags: CGEventFlags) {
        var modifiers: ModifierSet = []
        if flags.contains(.maskControl) { modifiers.insert(.control) }
        if flags.contains(.maskAlternate) { modifiers.insert(.option) }
        if flags.contains(.maskCommand) { modifiers.insert(.command) }
        if flags.contains(.maskShift) { modifiers.insert(.shift) }
        if flags.contains(.maskSecondaryFn) { modifiers.insert(.function) }
        self = modifiers
    }
}

/// The key whose tap writes for the user in the app in front.
public enum WriteKey: String, CaseIterable, Sendable, Identifiable {
    case rightOption, rightCommand, rightControl, rightShift
    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .rightOption: return "Right Option"
        case .rightCommand: return "Right Command"
        case .rightControl: return "Right Control"
        case .rightShift: return "Right Shift"
        }
    }

    /// The keyboard's own code for the key (not the same for the left and right key).
    public var keyCode: Int {
        switch self {
        case .rightOption: return 61
        case .rightCommand: return 54
        case .rightControl: return 62
        case .rightShift: return 60
        }
    }

    public var modifier: ModifierSet {
        switch self {
        case .rightOption: return .option
        case .rightCommand: return .command
        case .rightControl: return .control
        case .rightShift: return .shift
        }
    }
}

/// Recognises a *tap* or a *hold* of one modifier key: pressed and released alone.
///
/// A quick press is a tap (write now); a longer one is a hold (open the guidance bar). Like
/// it fires on release and only if nothing else happened meanwhile, so a key
/// used as part of a shortcut or to type a character (Option+E) never counts. The left key of
/// the same kind does not count either: only the key that was chosen.
public struct WriteKeyDetector {
    public enum Input: Equatable, Sendable {
        /// A modifier key changed: which key (its code), and the modifiers now held.
        case modifiers(keyCode: Int, held: ModifierSet, at: TimeInterval)
        /// A key or mouse button went down.
        case otherInput
    }

    public enum Gesture: Equatable, Sendable { case tap, hold }

    private let key: WriteKey
    private let tapLimit: TimeInterval
    private let holdLimit: TimeInterval
    private var downAt: TimeInterval?
    private var spoiled = false

    /// - Parameters:
    ///   - tapLimit: Longest press that is a tap; a longer one (up to `holdLimit`) is a hold.
    ///   - holdLimit: Longest press that counts at all: past it the user is doing something else.
    public init(key: WriteKey, tapLimit: TimeInterval = 0.35, holdLimit: TimeInterval = 4) {
        self.key = key
        self.tapLimit = tapLimit
        self.holdLimit = holdLimit
    }

    /// Feeds one input; returns the gesture exactly when the key has just been released after one.
    public mutating func handle(_ input: Input) -> Gesture? {
        switch input {
        case .otherInput:
            if downAt != nil { spoiled = true }
            return nil
        case let .modifiers(code, held, time):
            if held.isEmpty {
                defer { downAt = nil; spoiled = false }
                guard let start = downAt, !spoiled, code == key.keyCode else { return nil }
                let held = time - start
                return held <= tapLimit ? .tap : (held <= holdLimit ? .hold : nil)
            }
            if held == key.modifier, code == key.keyCode, downAt == nil, !spoiled {
                downAt = time
            } else if !(held == key.modifier && code == key.keyCode) {
                spoiled = true  // another key or modifier is involved: some other shortcut
                downAt = nil
            }
            return nil
        }
    }
}

/// Whether Tzara may write in other apps, and with which key. On by default: nothing happens
/// until the key is tapped, and nothing is ever sent.
public final class WritePreferences: @unchecked Sendable {
    static let enabledKey = "writeInOtherApps"
    static let keyKey = "writeKey"
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) { self.defaults = defaults }

    public var enabled: Bool {
        get { defaults.object(forKey: Self.enabledKey) as? Bool ?? true }
        set { defaults.set(newValue, forKey: Self.enabledKey) }
    }

    public var key: WriteKey {
        get { WriteKey(rawValue: defaults.string(forKey: Self.keyKey) ?? "") ?? .rightOption }
        set { defaults.set(newValue.rawValue, forKey: Self.keyKey) }
    }
}

/// The field and screen, as sent to `POST /v1/write`.
public struct WriteBody: Encodable, Equatable, Sendable {
    public var app = ""
    public var bundle = ""
    public var window = ""
    public var address = ""
    public var field = ""
    public var selection = ""
    public var visible = ""
    /// What the user typed in the guidance bar, if they used it.
    public var instruction = ""

    public init(
        app: String = "", bundle: String = "", window: String = "", address: String = "",
        field: String = "", selection: String = "", visible: String = "", instruction: String = ""
    ) {
        self.instruction = instruction
        self.app = app
        self.bundle = bundle
        self.window = window
        self.address = address
        self.field = field
        self.selection = selection
        self.visible = visible
    }
}

/// What the server wrote and what it takes the place of.
public struct WrittenText: Decodable, Equatable, Sendable {
    public let text: String
    public let mode: String
    public let place: String
    /// `selection`, `field` or `nothing`.
    public let replaces: String
}

extension Notification.Name {
    /// Posted when the user changes whether or with which key Tzara writes in other apps.
    public static let writeSettingsChanged = Notification.Name("tzara.writeSettingsChanged")
}
