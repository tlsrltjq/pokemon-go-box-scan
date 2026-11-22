// Live capture of one on-screen window (iPhone Mirroring or AirPlay) with ScreenCaptureKit.
// It only reads pixels on this Mac: no network, no input events.
//
//   window_capture list
//       One JSON line: {"windows": [{"id", "app", "bundle", "title", "width", "height"}]}
//       or {"error": "permission"} with exit code 2 when Screen Recording is not allowed.
//   window_capture stream <window id> <folder> <fps> <max seconds> <bar width>
//       Every 1/fps seconds the latest window image, with dark or transparent borders trimmed,
//       is written to stdout as a binary PPM scaled to <bar width> pixels wide (for the IV bars).
//       Once per second it is also saved as <folder>/frame-NNN.jpg, 720 px wide (for OCR).
//       Stops after <max seconds>, when stdout closes, or on SIGTERM.
import CoreImage
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

func emit(_ object: Any) {
    let data = try! JSONSerialization.data(withJSONObject: object)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

func shareable() async -> SCShareableContent {
    do {
        return try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
    } catch {
        emit(["error": "permission", "message": error.localizedDescription])
        exit(2)
    }
}

/// The phone screen inside the window: trims the window's own frame (iPhone Mirroring's gray border
/// and toolbar, AirPlay's black bars, transparent corners). The border colour is read at the
/// left edge; scan lines at 35/50/65 % of the other axis move inward until the colour changes.
func contentRect(_ image: CGImage) -> CGRect {
    let width = image.width, height = image.height
    let full = CGRect(x: 0, y: 0, width: width, height: height)
    guard width > 8, height > 8,
          let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                                  bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return full }
    context.draw(image, in: full)
    let pixels = context.data!.bindMemory(to: UInt8.self, capacity: width * height * 4)
    func pixel(_ x: Int, _ y: Int) -> (Int, Int, Int, Int) {
        let i = (y * width + x) * 4  // rows run top to bottom in memory
        return (Int(pixels[i]), Int(pixels[i + 1]), Int(pixels[i + 2]), Int(pixels[i + 3]))
    }
    let border = pixel(1, height / 2)
    func content(_ x: Int, _ y: Int) -> Bool {
        let p = pixel(x, y)
        if p.3 < 200 { return false }  // transparent counts as frame
        if border.3 < 200 { return true }
        return abs(p.0 - border.0) + abs(p.1 - border.1) + abs(p.2 - border.2) > 36
    }
    func inward(_ lines: [Int], _ limit: Int, _ at: (Int, Int) -> (Int, Int)) -> Int {
        // The outermost edge over the scan lines; two content pixels in a row end a line.
        lines.map { line in
            var step = 0
            while step < limit {
                let (x, y) = at(line, step), (x2, y2) = at(line, step + 1)
                if content(x, y) && content(x2, y2) { break }
                step += 1
            }
            return step
        }.min() ?? 0
    }
    let rows = [35, 50, 65].map { height * $0 / 100 }, columns = [35, 50, 65].map { width * $0 / 100 }
    let left = inward(rows, width / 3) { y, s in (s, y) }
    let right = inward(rows, width / 3) { y, s in (width - 1 - s, y) }
    let top = inward(columns, height / 3) { x, s in (x, s) }
    let bottom = inward(columns, height / 3) { x, s in (x, height - 1 - s) }
    let rect = CGRect(x: left, y: top, width: width - left - right, height: height - top - bottom)
    return rect.width >= CGFloat(width) * 0.5 && rect.height >= CGFloat(height) * 0.5 ? rect : full
}

func scaled(_ image: CGImage, width: Int) -> (CGContext, Int)? {
    let height = max(1, Int((Double(image.height) * Double(width) / Double(image.width)).rounded()))
    guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                                  bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
    context.interpolationQuality = .high
    context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
    return (context, height)
}

func ppm(_ image: CGImage, width: Int) -> Data? {
    guard let (context, height) = scaled(image, width: width) else { return nil }
    let source = context.data!.bindMemory(to: UInt8.self, capacity: width * height * 4)
    var data = Data("P6\n\(width) \(height)\n255\n".utf8)
    var rgb = [UInt8](repeating: 0, count: width * height * 3)
    for i in 0..<(width * height) {
        rgb[i * 3] = source[i * 4]
        rgb[i * 3 + 1] = source[i * 4 + 1]
        rgb[i * 3 + 2] = source[i * 4 + 2]
    }
    data.append(contentsOf: rgb)
    return data
}

func saveJPEG(_ image: CGImage, width: Int, to url: URL) {
    guard let (context, _) = scaled(image, width: width), let small = context.makeImage() else { return }
    let partial = url.appendingPathExtension("partial")
    guard let destination = CGImageDestinationCreateWithURL(partial as CFURL, UTType.jpeg.identifier as CFString, 1, nil)
    else { return }
    CGImageDestinationAddImage(destination, small, [kCGImageDestinationLossyCompressionQuality: 0.85] as CFDictionary)
    if CGImageDestinationFinalize(destination) {
        _ = try? FileManager.default.removeItem(at: url)
        try? FileManager.default.moveItem(at: partial, to: url)
    }
}

final class Receiver: NSObject, SCStreamOutput, SCStreamDelegate {
    private let lock = NSLock()
    private var latest: CGImage?
    private let context = CIContext()
    var stopped: ((String) -> Void)?

    func stream(_ stream: SCStream, didOutputSampleBuffer buffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, buffer.isValid, let pixels = buffer.imageBuffer else { return }
        // Idle frames repeat the previous image; only complete frames carry new pixels.
        if let attachments = CMSampleBufferGetSampleAttachmentsArray(buffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
           let raw = attachments.first?[.status] as? Int, raw != SCFrameStatus.complete.rawValue {
            return
        }
        let image = CIImage(cvPixelBuffer: pixels)
        guard let cg = context.createCGImage(image, from: image.extent) else { return }
        lock.lock(); latest = cg; lock.unlock()
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        stopped?(error.localizedDescription)
    }

    func current() -> CGImage? {
        lock.lock(); defer { lock.unlock() }
        return latest
    }
}

func streamWindow(id: UInt32, folder: URL, fps: Int, maxSeconds: Int, barWidth: Int) async {
    let content = await shareable()
    guard let window = content.windows.first(where: { $0.windowID == id }) else {
        fail("창을 찾지 못했습니다. 창 목록을 새로고침하세요.", code: 3)
    }
    let configuration = SCStreamConfiguration()
    let scale = 2.0
    configuration.width = Int(window.frame.width * scale)
    configuration.height = Int(window.frame.height * scale)
    configuration.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(fps))
    configuration.pixelFormat = kCVPixelFormatType_32BGRA
    configuration.showsCursor = false
    configuration.queueDepth = 4
    let receiver = Receiver()
    receiver.stopped = { message in fail("캡처가 멈췄습니다: \(message)", code: 4) }
    let stream = SCStream(filter: SCContentFilter(desktopIndependentWindow: window),
                          configuration: configuration, delegate: receiver)
    do {
        try stream.addStreamOutput(receiver, type: .screen, sampleHandlerQueue: DispatchQueue(label: "frames"))
        try await stream.startCapture()
    } catch {
        fail("캡처를 시작하지 못했습니다: \(error.localizedDescription)", code: 4)
    }
    signal(SIGPIPE, SIG_IGN)
    let ticks = fps * maxSeconds
    var crop: CGRect?
    var tick = 0
    let start = DispatchTime.now().uptimeNanoseconds
    while tick < ticks {
        let due = start + UInt64(tick) * 1_000_000_000 / UInt64(fps)
        let now = DispatchTime.now().uptimeNanoseconds
        if due > now { try? await Task.sleep(nanoseconds: due - now) }
        guard let image = receiver.current() else {
            // Nothing captured yet: wait without counting the tick, so time starts at the first image.
            try? await Task.sleep(nanoseconds: 50_000_000)
            if tick == 0 { continue }
            tick += 1
            continue
        }
        if tick % fps == 0 || crop == nil {
            crop = contentRect(image)
        }
        let frame = image.cropping(to: crop!) ?? image
        if tick % fps == 0 {
            let number = tick / fps + 1
            saveJPEG(frame, width: 720, to: folder.appendingPathComponent(String(format: "frame-%03d.jpg", number)))
        }
        guard let data = ppm(frame, width: barWidth) else { tick += 1; continue }
        do {
            try FileHandle.standardOutput.write(contentsOf: data)
        } catch {
            break  // the reader went away: stop quietly
        }
        tick += 1
    }
    try? await stream.stopCapture()
}

let arguments = CommandLine.arguments
switch arguments.count > 1 ? arguments[1] : "" {
case "list":
    let content = await shareable()
    let own = ProcessInfo.processInfo.processIdentifier
    let windows: [[String: Any]] = content.windows.compactMap { window in
        guard window.windowLayer == 0, window.frame.width >= 150, window.frame.height >= 250,
              let app = window.owningApplication, app.processID != own else { return nil }
        return ["id": Int(window.windowID), "app": app.applicationName, "bundle": app.bundleIdentifier,
                "title": window.title ?? "", "width": Int(window.frame.width), "height": Int(window.frame.height)]
    }
    emit(["windows": windows])
case "stream" where arguments.count == 7:
    guard let id = UInt32(arguments[2]), let fps = Int(arguments[4]), let seconds = Int(arguments[5]),
          let barWidth = Int(arguments[6]), (1...30).contains(fps), (1...3600).contains(seconds) else {
        fail("인자가 올바르지 않습니다.")
    }
    signal(SIGTERM) { _ in exit(0) }
    await streamWindow(id: id, folder: URL(fileURLWithPath: arguments[3]), fps: fps, maxSeconds: seconds,
                       barWidth: barWidth)
default:
    fail("usage: window_capture list | stream <window id> <folder> <fps> <max seconds> <bar width>")
}
