import CoreGraphics
import Testing

@testable import TzaraCore

private let both: ModifierSet = [.control, .option]

/// Plays a sequence of inputs through a detector and reports how many taps it saw.
private func taps(
    _ steps: [ChordDetector.Input], configuration: ChordDetector.Configuration = .init()
) -> Int {
    var detector = ChordDetector(configuration: configuration)
    return steps.reduce(0) { count, step in count + (detector.handle(step) ? 1 : 0) }
}

private func mods(_ modifiers: ModifierSet, _ time: Double) -> ChordDetector.Input {
    .modifiersChanged(modifiers, at: time)
}

@Suite("ChordDetector")
struct ChordDetectorTests {
    @Test func tappingTheChordFiresOnceWhenItIsReleased() {
        #expect(taps([mods(.control, 0.0), mods(both, 0.05), mods(.control, 0.2), mods([], 0.25)]) == 1)
    }

    @Test func itFiresOnTheReleaseNotOnThePress() {
        var detector = ChordDetector()
        #expect(detector.handle(mods(.option, 0.0)) == false)
        #expect(detector.handle(mods(both, 0.05)) == false)  // both are down, but not released yet
        #expect(detector.handle(mods(.option, 0.2)) == true)
    }

    @Test func theOrderOfPressingAndReleasingDoesNotMatter() {
        #expect(taps([mods(.option, 0), mods(both, 0.1), mods(.option, 0.2), mods([], 0.3)]) == 1)
        #expect(taps([mods(.control, 0), mods(both, 0.1), mods(.option, 0.2), mods([], 0.3)]) == 1)
    }

    @Test func releasingBothAtOnceStillCounts() {
        #expect(taps([mods(both, 0.0), mods([], 0.2)]) == 1)
    }

    @Test func aKeyPressedWhileHeldSpoilsTheTap() {
        // Control+Option+Arrow: a window-manager shortcut, not ours.
        #expect(taps([mods(both, 0), .otherInput, mods(.control, 0.3), mods([], 0.35)]) == 0)
    }

    @Test func aMouseClickWhileHeldSpoilsTheTapToo() {
        #expect(taps([mods(both, 0), .otherInput, mods([], 0.3)]) == 0)
    }

    @Test func aKeyPressedBeforeTheSecondModifierSpoilsIt() {
        // Control+C, then Option goes down: the chord never started cleanly.
        #expect(taps([mods(.control, 0), .otherInput, mods(both, 0.1), mods([], 0.2)]) == 0)
    }

    @Test func ordinaryTypingWithNoModifiersIsIgnored() {
        #expect(taps([.otherInput, .otherInput, mods(both, 1), mods([], 1.2)]) == 1)
    }

    @Test func anExtraModifierSpoilsTheTap() {
        #expect(taps([mods(both, 0), mods([.control, .option, .shift], 0.1), mods(both, 0.2), mods([], 0.3)]) == 0)
        #expect(taps([mods([.control, .command], 0), mods([.control, .option, .command], 0.1), mods([], 0.2)]) == 0)
    }

    @Test func holdingTheChordTooLongIsNotATap() {
        #expect(taps([mods(both, 0), mods(.control, 1.5), mods([], 1.6)]) == 0)
    }

    @Test func theMaximumHoldIsConfigurable() {
        let patient = ChordDetector.Configuration(maximumHold: 5)
        #expect(taps([mods(both, 0), mods(.control, 3), mods([], 3.1)], configuration: patient) == 1)
        let impatient = ChordDetector.Configuration(maximumHold: 0.1)
        #expect(taps([mods(both, 0), mods(.control, 0.5), mods([], 0.6)], configuration: impatient) == 0)
    }

    @Test func aHoldOfExactlyTheMaximumStillCounts() {
        #expect(taps([mods(both, 0), mods([], 1.0)]) == 1)
    }

    @Test func pressingTheReleasedKeyAgainWhileTheOtherIsHeldDoesNotFireAgain() {
        let sequence = [mods(both, 0), mods(.control, 0.1), mods(both, 0.2), mods(.control, 0.3), mods([], 0.4)]
        #expect(taps(sequence) == 1)
    }

    @Test func eachFreshTapFires() {
        let sequence = [mods(both, 0), mods([], 0.2), mods(both, 1), mods([], 1.2), mods(both, 2), mods([], 2.2)]
        #expect(taps(sequence) == 3)
    }

    @Test func aSpoiledPressDoesNotAffectTheNextOne() {
        let sequence = [mods(both, 0), .otherInput, mods([], 0.3), mods(both, 1), mods([], 1.2)]
        #expect(taps(sequence) == 1)
    }

    @Test func aSingleModifierAloneIsNotAChord() {
        #expect(taps([mods(.control, 0), mods([], 0.1), mods(.option, 1), mods([], 1.1)]) == 0)
    }

    @Test func aDifferentChordCanBeConfigured() {
        let commandShift = ChordDetector.Configuration(chord: [.command, .shift])
        #expect(taps([mods([.command, .shift], 0), mods([], 0.1)], configuration: commandShift) == 1)
        #expect(taps([mods(both, 0), mods([], 0.1)], configuration: commandShift) == 0)
    }
}

@Suite("ModifierSet from event flags")
struct ModifierSetFlagTests {
    @Test func mapsEachModifier() {
        #expect(ModifierSet(flags: .maskControl) == .control)
        #expect(ModifierSet(flags: .maskAlternate) == .option)
        #expect(ModifierSet(flags: .maskCommand) == .command)
        #expect(ModifierSet(flags: .maskShift) == .shift)
        #expect(ModifierSet(flags: .maskSecondaryFn) == .function)
    }

    @Test func combinesThem() {
        #expect(ModifierSet(flags: [.maskControl, .maskAlternate]) == [.control, .option])
    }

    @Test func capsLockAndNoFlagsAreNotModifiers() {
        #expect(ModifierSet(flags: .maskAlphaShift).isEmpty)
        #expect(ModifierSet(flags: []).isEmpty)
    }
}
