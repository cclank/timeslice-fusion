#!/usr/bin/env swift
// remove_bg.swift — macOS Vision 人像抠图 (发丝级精度)
// Usage: swift remove_bg.swift <input_image> <output_png>

import Foundation
import AppKit
import Vision
import CoreImage

guard CommandLine.arguments.count >= 3 else {
    fputs("Usage: swift remove_bg.swift <input_image> <output_png>\n", stderr)
    exit(1)
}

let inputPath = CommandLine.arguments[1]
let outputPath = CommandLine.arguments[2]

guard let inputImage = NSImage(contentsOfFile: inputPath) else {
    fputs("Error: Cannot load image: \(inputPath)\n", stderr)
    exit(1)
}

guard let cgImage = inputImage.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fputs("Error: Cannot convert to CGImage\n", stderr)
    exit(1)
}

let request = VNGeneratePersonSegmentationRequest()
request.qualityLevel = .accurate
request.outputPixelFormat = kCVPixelFormatType_OneComponent8

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])

do {
    try handler.perform([request])
} catch {
    fputs("Error: Vision request failed: \(error)\n", stderr)
    exit(1)
}

guard let result = request.results?.first else {
    fputs("Error: No person detected in image\n", stderr)
    exit(1)
}

let maskBuffer = result.pixelBuffer
let ciMask = CIImage(cvPixelBuffer: maskBuffer)
let ciInput = CIImage(cgImage: cgImage)

// Scale mask to match input image size
let scaleX = CGFloat(cgImage.width) / ciMask.extent.width
let scaleY = CGFloat(cgImage.height) / ciMask.extent.height
let scaledMask = ciMask.transformed(by: CGAffineTransform(scaleX: scaleX, y: scaleY))

// Apply mask: blend input over transparent using the mask
guard let blendFilter = CIFilter(name: "CIBlendWithMask") else {
    fputs("Error: Cannot create blend filter\n", stderr)
    exit(1)
}

let transparentImage = CIImage(color: CIColor.clear).cropped(to: ciInput.extent)

blendFilter.setValue(ciInput, forKey: kCIInputImageKey)
blendFilter.setValue(transparentImage, forKey: kCIInputBackgroundImageKey)
blendFilter.setValue(scaledMask, forKey: kCIInputMaskImageKey)

guard let outputCIImage = blendFilter.outputImage else {
    fputs("Error: Blend filter produced no output\n", stderr)
    exit(1)
}

let context = CIContext()
guard let outputCGImage = context.createCGImage(outputCIImage, from: ciInput.extent) else {
    fputs("Error: Cannot create output CGImage\n", stderr)
    exit(1)
}

// Save as PNG with transparency
let rep = NSBitmapImageRep(cgImage: outputCGImage)
rep.size = NSSize(width: cgImage.width, height: cgImage.height)
guard let pngData = rep.representation(using: .png, properties: [:]) else {
    fputs("Error: Cannot create PNG data\n", stderr)
    exit(1)
}

do {
    try pngData.write(to: URL(fileURLWithPath: outputPath))
    print("OK: \(outputPath)")
} catch {
    fputs("Error: Cannot write output: \(error)\n", stderr)
    exit(1)
}
