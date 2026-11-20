package com.djound.rendition;

import java.io.EOFException;
import java.io.IOException;
import java.nio.file.Path;
import javax.sound.sampled.AudioFormat;
import javax.sound.sampled.AudioSystem;
import javax.sound.sampled.UnsupportedAudioFileException;

/** A compact envelope for the player, read directly from the verified immutable stereo WAV. */
final class RenditionWaveform {
  private RenditionWaveform() {}

  static int[] peaks(Path path) throws IOException {
    try (var stream = AudioSystem.getAudioInputStream(path.toFile())) {
      var format = stream.getFormat();
      long frames = stream.getFrameLength();
      if (frames <= 0
          || !AudioFormat.Encoding.PCM_SIGNED.equals(format.getEncoding())
          || format.isBigEndian()
          || format.getSampleSizeInBits() != 16
          || format.getFrameSize() != 4
          || format.getChannels() != 2) {
        throw new IOException("Cannot read the saved stereo waveform.");
      }
      int[] peaks = new int[(int) Math.min(512, frames)];
      byte[] block = new byte[16 * 1024];
      long position = 0;
      for (int bin = 0; bin < peaks.length; bin++) {
        long end = (bin + 1L) * frames / peaks.length;
        while (position < end) {
          int bytes = (int) Math.min(block.length, (end - position) * 4);
          int read = stream.readNBytes(block, 0, bytes);
          if (read != bytes) throw new EOFException("The saved waveform is truncated.");
          for (int i = 0; i < read; i += 2) {
            int sample = (block[i + 1] << 8) | (block[i] & 0xff);
            peaks[bin] = Math.max(peaks[bin], Math.abs(sample));
          }
          position += read / 4;
        }
      }
      return peaks;
    } catch (UnsupportedAudioFileException e) {
      throw new IOException("Cannot read the saved waveform.", e);
    }
  }
}
