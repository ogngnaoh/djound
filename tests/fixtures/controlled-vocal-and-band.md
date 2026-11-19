# A sung melody under an electric guitar

This 24-second stereo fixture combines Dcoetzee’s a cappella performance of “Twinkle, Twinkle, Little Star” with a fixed 108 BPM arrangement for clean electric guitar, electric piano, acoustic bass and drums. The guitar plays a higher countermelody. The combined backing sits 3 dB below the vocal in RMS level. The source performance is dedicated to the public domain under CC0 1.0. [Original recording and license](https://commons.wikimedia.org/wiki/File:Twinkle_Twinkle_Little_Star_-_sung_with_full_lyrics.ogg)

The opt-in regression runs locally after the rendition engine and its models have been installed. From the repository root, run `.runtime/local-engine/venv/bin/python tests/sung_melody_acceptance.py`. The test decodes the bundled lossless mix, checks its PCM digest, and transcribes the complete recording. It scores the first four lyric lines against the written tune. The syllable boundaries were estimated from the separate vocal recording.

Djound’s original backing arrangement and mix are distributed under the project’s MIT license. The backing was rendered with Frank Wen’s FluidR3 GM instrument bank. Contributors acknowledged by the bank’s author include Suren M. Seron, Scott Hanan, Steve Aupperle, Chris Gillman, Alex Taubr, Chris Prola, Andrew Klenk, Winfried Hubbe, Dylan, Tim, Gort, Uros Katic and Ethan Winer. [Project license](../../LICENSE) · [Bank credits](https://github.com/pianobooster/fluid-soundfont/blob/main/README) · [Bank license](https://github.com/pianobooster/fluid-soundfont/blob/main/COPYING)

```
Copyright (c) 2000-2002, 2008 Frank Wen <getfrank@gmail.com>

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.
```
