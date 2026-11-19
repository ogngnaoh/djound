/* Render one scored part to an unclipped stereo float stream. The Python mixer
   balances the entire performance with fixed gains before converting to PCM. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#define TSF_IMPLEMENTATION
#include "tsf.h"

static int fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    return 1;
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--version")) { puts("1"); return 0; }
    if (argc != 4) return fail("Usage: score_renderer soundfont schedule output.f32");
    FILE *score = fopen(argv[2], "r");
    int frames, bank, program, channels, count;
    if (!score || fscanf(score, "%d%d%d%d%d", &frames, &bank, &program, &channels, &count) != 5 ||
        frames < 1 || frames > 600 * 44100 || (bank != 0 && bank != 128) ||
        program < 0 || program > 127 || channels < 1 || channels > 128 ||
        count < 2 || count > 131072) return fail("Invalid performance schedule.");
    tsf *font = tsf_load_filename(argv[1]);
    if (!font) return fail("Cannot load the score's instrument bank.");
    tsf_set_output(font, TSF_STEREO_INTERLEAVED, 44100, -4.0f);
    if (!tsf_set_max_voices(font, 512)) return fail("Cannot allocate score voices.");
    for (int ch = 0; ch < channels; ++ch)
        if (!tsf_channel_set_bank_preset(font, ch, bank, program))
            return fail("The requested instrument or drum kit is absent from the soundfont.");
    FILE *out = fopen(argv[3], "wbx");
    if (!out) return fail("Cannot create the performance scratch file.");
    float samples[1024];
    double sum_squares = 0;
    int position = 0;
    for (int i = 0; i <= count; ++i) {
        int frame = frames, channel = 0, pitch = 0, velocity = 0;
        if (i < count && (fscanf(score, "%d%d%d%d", &frame, &channel, &pitch, &velocity) != 4 ||
            frame < position || frame > frames || channel < 0 || channel >= channels ||
            pitch < 0 || pitch > 127 || velocity < 0 || velocity > 127))
            return fail("Invalid note event in the performance.");
        while (position < frame) {
            int length = frame - position < 512 ? frame - position : 512;
            tsf_render_float(font, samples, length, 0);
            for (int j = 0; j < 2 * length; ++j) sum_squares += (double)samples[j] * samples[j];
            if (fwrite(samples, 2 * sizeof(float), length, out) != (size_t)length)
                return fail("Cannot write score samples.");
            position += length;
        }
        if (i == count) break;
        if (!velocity) tsf_channel_note_off(font, channel, pitch);
        else if (!tsf_channel_note_on(font, channel, pitch, velocity / 127.0f))
            return fail("Cannot play the requested score note.");
    }
    fclose(score);
    tsf_close(font);
    if (fclose(out) || !isfinite(sum_squares)) return fail("Cannot finish the score render.");
    printf("%.12g\n", sqrt(sum_squares / (frames * 2.0)));
    return 0;
}
