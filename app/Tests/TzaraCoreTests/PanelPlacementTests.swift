import CoreGraphics
import Testing

@testable import TzaraCore

private let screen = CGRect(x: 0, y: 0, width: 1440, height: 900)  // Cocoa: y grows upwards
private let size = CGSize(width: 380, height: 440)
private let placement = PanelPlacement(pointerOffset: CGPoint(x: 28, y: 22), margin: 8)

private func isOnScreen(_ frame: CGRect, within bounds: CGRect, margin: CGFloat = 8) -> Bool {
    bounds.insetBy(dx: margin, dy: margin).contains(frame)
}

@Suite("PanelPlacement")
struct PanelPlacementTests {
    @Test func theInputLineLandsUnderThePointer() {
        let cursor = CGPoint(x: 600, y: 400)
        let frame = placement.frame(cursor: cursor, size: size, screens: [screen])
        #expect(frame.minX == cursor.x - 28)  // pointer near the left end of the input line
        #expect(frame.minY == cursor.y - 22)  // and level with it
        #expect(frame.contains(cursor))
        #expect(frame.size == size)
    }

    @Test func theConversationExtendsUpwardsFromThePointer() {
        let cursor = CGPoint(x: 600, y: 300)
        let frame = placement.frame(cursor: cursor, size: size, screens: [screen])
        #expect(frame.maxY > cursor.y + 300)
    }

    @Test func nearTheRightEdgeTheBoxIsMirroredSoThePointerIsAtTheRightEndOfTheInput() {
        let cursor = CGPoint(x: 1400, y: 500)
        let frame = placement.frame(cursor: cursor, size: size, screens: [screen])
        #expect(frame.maxX == cursor.x + 28)
        #expect(frame.contains(cursor))
        #expect(isOnScreen(frame, within: screen))
    }

    @Test func nearTheBottomEdgeTheBoxIsPushedUpButStillCoversThePointer() {
        let cursor = CGPoint(x: 600, y: 10)
        let frame = placement.frame(cursor: cursor, size: size, screens: [screen])
        #expect(frame.minY == 8)  // the margin
        #expect(frame.contains(cursor))
    }

    @Test func nearTheTopEdgeTheBoxIsPushedDown() {
        let cursor = CGPoint(x: 600, y: 895)
        let frame = placement.frame(cursor: cursor, size: size, screens: [screen])
        #expect(frame.maxY == screen.maxY - 8)
        #expect(isOnScreen(frame, within: screen))
    }

    @Test func staysOnScreenInEveryCorner() {
        for cursor in [CGPoint(x: 1, y: 1), CGPoint(x: 1439, y: 1), CGPoint(x: 1, y: 899), CGPoint(x: 1439, y: 899)] {
            let frame = placement.frame(cursor: cursor, size: size, screens: [screen])
            #expect(isOnScreen(frame, within: screen), "corner \(cursor)")
        }
    }

    @Test func usesTheScreenThePointerIsOn() {
        let left = CGRect(x: -1920, y: 0, width: 1920, height: 1080)
        let frame = placement.frame(cursor: CGPoint(x: -900, y: 600), size: size, screens: [screen, left])
        #expect(isOnScreen(frame, within: left))
        #expect(frame.maxX < 0)
        #expect(frame.contains(CGPoint(x: -900, y: 600)))
    }

    @Test func staysOnTheSecondScreenNearItsRightEdge() {
        let left = CGRect(x: -1920, y: 0, width: 1920, height: 1080)
        let frame = placement.frame(cursor: CGPoint(x: -10, y: 600), size: size, screens: [screen, left])
        #expect(isOnScreen(frame, within: left))
    }

    @Test func aPointerBetweenScreensUsesTheNearestOne() {
        let right = CGRect(x: 1500, y: 0, width: 1000, height: 800)  // a gap after the first screen
        let frame = placement.frame(cursor: CGPoint(x: 1470, y: 400), size: size, screens: [screen, right])
        #expect(isOnScreen(frame, within: screen) || isOnScreen(frame, within: right))
    }

    @Test func aBoxBiggerThanTheScreenIsPinnedToItsBottomLeft() {
        let tiny = CGRect(x: 0, y: 0, width: 300, height: 300)
        let frame = placement.frame(cursor: CGPoint(x: 150, y: 150), size: size, screens: [tiny])
        #expect(frame.minX == 8)
        #expect(frame.minY == 8)
    }

    @Test func worksWithNoScreensAtAll() {
        let frame = placement.frame(cursor: CGPoint(x: 100, y: 100), size: size, screens: [])
        #expect(frame.size == size)
    }

    @Test func respectsTheConfiguredMarginAndOffset() {
        let custom = PanelPlacement(pointerOffset: CGPoint(x: 50, y: 40), margin: 40)
        let open = custom.frame(cursor: CGPoint(x: 600, y: 400), size: size, screens: [screen])
        #expect(open.minX == 550 && open.minY == 360)
        let edge = custom.frame(cursor: CGPoint(x: 1435, y: 450), size: size, screens: [screen])
        #expect(edge.minX >= 40 && edge.maxX <= screen.maxX - 40)
    }

    @Test(arguments: [
        CGPoint(x: 0, y: 0), CGPoint(x: 1439, y: 0), CGPoint(x: 0, y: 899), CGPoint(x: 1439, y: 899),
        CGPoint(x: 720, y: 450), CGPoint(x: 1, y: 450), CGPoint(x: 1438, y: 450), CGPoint(x: 720, y: 5),
    ])
    func theFrameIsAlwaysFullyOnScreen(cursor: CGPoint) {
        let frame = placement.frame(cursor: cursor, size: size, screens: [screen])
        #expect(isOnScreen(frame, within: screen))
    }
}
