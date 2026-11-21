import CoreGraphics

/// Decides where the chat box goes: so the pointer lands on its input line, and the box is
/// always fully on screen.
///
/// The box has the conversation above and the input line at the bottom, so opening it with
/// the input line under the pointer means there is nothing to move the mouse to: type, or
/// click right where you are.
///
/// Coordinates are Cocoa's: the origin is the bottom-left of the main screen and *y grows
/// upwards*, which is what `NSEvent.mouseLocation` and `NSScreen.visibleFrame` use.
public struct PanelPlacement: Equatable, Sendable {
    /// Where the pointer should land inside the box, measured from the box's bottom-left
    /// corner: near the left end of the input line.
    public var pointerOffset: CGPoint
    /// Closest the box may come to the edge of a screen.
    public var margin: CGFloat

    public init(pointerOffset: CGPoint = CGPoint(x: 28, y: 22), margin: CGFloat = 8) {
        self.pointerOffset = pointerOffset
        self.margin = margin
    }

    /// The frame for a box of `size` when the mouse is at `cursor`.
    ///
    /// Near the right edge the box is mirrored, so the pointer lands near the *right* end of
    /// the input line instead. Near any edge the box is then pushed back inside the screen,
    /// which moves the input line off the pointer as little as it can.
    ///
    /// - Parameters:
    ///   - cursor: Mouse position.
    ///   - size: Size of the box.
    ///   - screens: Usable area of each screen (`NSScreen.visibleFrame`).
    public func frame(cursor: CGPoint, size: CGSize, screens: [CGRect]) -> CGRect {
        let area = usableArea(for: cursor, screens: screens, size: size)

        var x = cursor.x - pointerOffset.x
        if x + size.width > area.maxX {
            x = cursor.x - (size.width - pointerOffset.x)
        }
        var y = cursor.y - pointerOffset.y

        // Last resort: a box bigger than the room is pinned to the screen's bottom-left corner.
        x = max(area.minX, min(x, area.maxX - size.width))
        y = max(area.minY, min(y, area.maxY - size.height))
        return CGRect(origin: CGPoint(x: x, y: y), size: size)
    }

    private func usableArea(for cursor: CGPoint, screens: [CGRect], size: CGSize) -> CGRect {
        let screen = screens.first { $0.contains(cursor) } ?? nearest(to: cursor, in: screens)
        guard let screen else { return CGRect(origin: .zero, size: size) }
        return screen.insetBy(dx: margin, dy: margin)
    }

    private func nearest(to point: CGPoint, in screens: [CGRect]) -> CGRect? {
        screens.min { distance(from: point, to: $0) < distance(from: point, to: $1) }
    }

    private func distance(from point: CGPoint, to rect: CGRect) -> CGFloat {
        let dx = max(rect.minX - point.x, 0, point.x - rect.maxX)
        let dy = max(rect.minY - point.y, 0, point.y - rect.maxY)
        return (dx * dx + dy * dy).squareRoot()
    }
}
