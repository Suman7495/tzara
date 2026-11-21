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

/// Recognises a *tap* of a modifier chord, such as Control+Option.
///
/// The chord fires when its keys are released, not when they go down, and only if nothing
/// else happened while they were held. That keeps it out of the way of real shortcuts that
/// start with the same keys (window managers use Control+Option+Arrow, for instance): those
/// press a key while the modifiers are down, which spoils the tap.
///
/// A tap is spoiled by another key or a mouse click while the chord is held, by any extra
/// modifier, and by holding the chord longer than ``Configuration/maximumHold``.
public struct ChordDetector {
    public struct Configuration: Equatable, Sendable {
        /// The modifiers that make up the chord.
        public var chord: ModifierSet
        /// Longest the chord may be held and still count as a tap, in seconds.
        public var maximumHold: TimeInterval

        public init(chord: ModifierSet = [.control, .option], maximumHold: TimeInterval = 1.0) {
            self.chord = chord
            self.maximumHold = maximumHold
        }
    }

    /// What the detector is told about.
    public enum Input: Equatable, Sendable {
        /// The set of modifier keys held down changed. `at` is a monotonic time in seconds.
        case modifiersChanged(ModifierSet, at: TimeInterval)
        /// A key or mouse button went down.
        case otherInput
    }

    private let configuration: Configuration
    private var held: ModifierSet = []
    private var armedAt: TimeInterval?
    private var spoiled = false

    public init(configuration: Configuration = Configuration()) {
        self.configuration = configuration
    }

    /// Feeds one input to the detector.
    ///
    /// - Returns: `true` exactly when the chord has just been tapped.
    public mutating func handle(_ input: Input) -> Bool {
        switch input {
        case .otherInput:
            if !held.isEmpty { spoil() }
            return false
        case let .modifiersChanged(modifiers, time):
            return handleModifiers(modifiers, at: time)
        }
    }

    private mutating func handleModifiers(_ modifiers: ModifierSet, at time: TimeInterval) -> Bool {
        let previous = held
        let armedStart = armedAt
        held = modifiers

        // Another modifier is involved: this is some other shortcut.
        guard modifiers.isSubset(of: configuration.chord) else {
            spoil()
            return false
        }

        // The whole chord is down: start timing, unless this press is already spoiled.
        if modifiers == configuration.chord {
            if !spoiled && armedAt == nil { armedAt = time }
            return false
        }

        // Fewer keys than the chord. Either still building up to it, or one was released.
        let released = modifiers.isStrictSubset(of: previous)
        armedAt = nil
        if modifiers.isEmpty {
            spoiled = false
        } else if released {
            spoiled = true  // stay quiet until every modifier is up again
        }
        guard released, let start = armedStart else { return false }
        return time - start <= configuration.maximumHold
    }

    private mutating func spoil() {
        spoiled = true
        armedAt = nil
    }
}
