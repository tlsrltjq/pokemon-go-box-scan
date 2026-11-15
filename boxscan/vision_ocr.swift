// Local text recognition with Apple Vision. No network access.
// Usage: vision_ocr <image>...
// Prints one JSON object per image:
// {"path", "width", "height", "lines": [{"text", "confidence", "box": [x, y, w, h]}], "error"}
// Boxes use pixel units with a top-left origin.
import AppKit
import Foundation
import Vision

func emit(_ object: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.recognitionLanguages = ["ko-KR", "en-US"]

for path in CommandLine.arguments.dropFirst() {
    guard let image = NSImage(contentsOfFile: path),
          let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        emit(["path": path, "lines": [], "error": "unreadable image"])
        continue
    }
    let width = Double(cgImage.width), height = Double(cgImage.height)
    do {
        try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
    } catch {
        emit(["path": path, "width": Int(width), "height": Int(height), "lines": [],
              "error": error.localizedDescription])
        continue
    }
    var lines: [[String: Any]] = []
    for observation in request.results ?? [] {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let box = observation.boundingBox
        lines.append([
            "text": candidate.string,
            "confidence": Double(candidate.confidence),
            "box": [Int(box.minX * width), Int((1 - box.maxY) * height),
                    Int(box.width * width), Int(box.height * height)],
        ])
    }
    lines.sort { ($0["box"] as! [Int])[1] < ($1["box"] as! [Int])[1] }
    emit(["path": path, "width": Int(width), "height": Int(height), "lines": lines])
}
