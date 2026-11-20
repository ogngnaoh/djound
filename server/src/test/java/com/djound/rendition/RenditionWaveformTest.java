package com.djound.rendition;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;

import java.io.ByteArrayInputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Path;
import javax.sound.sampled.AudioFileFormat;
import javax.sound.sampled.AudioFormat;
import javax.sound.sampled.AudioInputStream;
import javax.sound.sampled.AudioSystem;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class RenditionWaveformTest {
  @TempDir Path directory;

  @Test
  void measuresBothChannelsAndIncludesTheFinalPartialInterval() throws Exception {
    int frames = 1025;
    var pcm = ByteBuffer.allocate(frames * 4).order(ByteOrder.LITTLE_ENDIAN);
    pcm.putShort(0, (short) -12000);
    pcm.putShort(2, (short) 18001);
    pcm.putShort(4 * 2, Short.MIN_VALUE);
    pcm.putShort(4 * (frames - 1) + 2, (short) -9000);
    var path = directory.resolve("stereo.wav");
    var format = new AudioFormat(44100, 16, 2, true, false);
    try (var stream = new AudioInputStream(new ByteArrayInputStream(pcm.array()), format, frames)) {
      AudioSystem.write(stream, AudioFileFormat.Type.WAVE, path.toFile());
    }
    int[] expected = new int[512];
    expected[0] = 18001;
    expected[1] = 32768;
    expected[511] = 9000;
    assertArrayEquals(expected, RenditionWaveform.peaks(path));
  }
}
