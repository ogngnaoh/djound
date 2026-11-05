/* v2: isolated track scheduling, shared upstream sample bank, explicit raw stems. */
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#define TSF_IMPLEMENTATION
#include "tsf.h"

#define MAX_EVENTS 16384
typedef struct { int frame, channel, on, pitch, velocity; } Event;

static int fail(const char *message) { fprintf(stderr, "%s\n", message); return 1; }

int main(int argc, char **argv) {
    if (argc != 4) return fail("Usage: renderer soundfont events.txt output.f32");
    FILE *events = fopen(argv[2], "r");
    if (!events) return fail("Cannot read events");
    int frames, tracks, count;
    if (fscanf(events, "%d %d %d", &frames, &tracks, &count) != 3 ||
        frames < 1 || frames > 12 * 44100 || tracks < 1 || tracks > 8 ||
        count < 2 || count > MAX_EVENTS) return fail("Invalid event header");
    int banks[8], programs[8];
    for (int i = 0; i < tracks; ++i)
        if (fscanf(events, "%d %d", &banks[i], &programs[i]) != 2 ||
            (banks[i] != 0 && banks[i] != 128) || programs[i] < 0 || programs[i] > 127)
            return fail("Invalid program");
    Event *list = calloc((size_t)count, sizeof(Event));
    if (!list) return fail("Cannot allocate events");
    for (int i = 0; i < count; ++i) {
        Event *e = &list[i];
        if (fscanf(events, "%d %d %d %d %d", &e->frame, &e->channel, &e->on, &e->pitch, &e->velocity) != 5 ||
            e->frame < 0 || e->frame > frames || e->channel < 0 || e->channel >= tracks ||
            (e->on != 0 && e->on != 1) || e->pitch < 0 || e->pitch > 127 ||
            e->velocity < 1 || e->velocity > 127 || (i && e->frame < list[i-1].frame))
            return fail("Invalid or unsorted event");
    }
    fclose(events);
    tsf *bank = tsf_load_filename(argv[1]);
    if (!bank) return fail("Cannot load pinned soundfont");
    tsf_set_output(bank, TSF_STEREO_INTERLEAVED, 44100, -6.0f);
    double *mix = calloc((size_t)frames * 2, sizeof(double));
    if (!mix) return fail("Cannot allocate bounded mix");
    float buffer[1024];
    for (int track = 0; track < tracks; ++track) {
        /* tsf_copy shares immutable samples/presets, but has independent voices/channels. */
        tsf *synth = tsf_copy(bank);
        if (!synth || !tsf_set_max_voices(synth, 128)) return fail("Cannot allocate track voices");
        if (!tsf_channel_set_bank_preset(synth, 0, banks[track], programs[track]))
            return fail("Requested bank/program not present");
        fprintf(stderr, "PRESET\t%d\t%d\t%d\t%s\n", track, banks[track], programs[track],
                tsf_bank_get_presetname(synth, banks[track], programs[track]));
        char stem_path[4096];
        int length = snprintf(stem_path, sizeof(stem_path), "%s.track-%d.f32", argv[3], track);
        if (length < 0 || (size_t)length >= sizeof(stem_path)) return fail("Stem path too long");
        FILE *stem = fopen(stem_path, "wbx");
        if (!stem) return fail("Cannot create new stem");
        int position = 0, index = 0;
        while (position < frames) {
            while (index < count && list[index].channel != track) ++index;
            while (index < count && list[index].frame == position) {
                Event *e = &list[index++];
                if (e->on) {
                    if (!tsf_channel_note_on(synth, 0, e->pitch, e->velocity / 127.0f))
                        return fail("Cannot allocate note voice");
                } else tsf_channel_note_off(synth, 0, e->pitch);
                while (index < count && list[index].channel != track) ++index;
            }
            int next = (index < count ? list[index].frame : frames);
            int block = next - position;
            if (block > 512) block = 512;
            if (block < 1) return fail("Non-progressing event schedule");
            tsf_render_float(synth, buffer, block, 0);
            for (int sample = 0; sample < 2 * block; ++sample) {
                int frame = position + sample / 2;
                int fade_frames = (frames < 882 ? frames : 882);
                if (frame >= frames - fade_frames)
                    buffer[sample] *= (fade_frames > 1 ? (float)(frames - 1 - frame) / (fade_frames - 1) : 0.0f);
                if (!isfinite(buffer[sample])) return fail("Nonfinite synthesized sample");
                mix[2 * position + sample] += (double)buffer[sample];
            }
            if (fwrite(buffer, sizeof(float), (size_t)(2 * block), stem) != (size_t)(2 * block))
                return fail("Cannot write stem");
            position += block;
        }
        if (fclose(stem)) return fail("Cannot finish stem");
        tsf_close(synth);
    }
    FILE *output = fopen(argv[3], "wbx");
    if (!output) return fail("Cannot create new output");
    for (int sample = 0; sample < frames * 2; ++sample) {
        float value = (float)mix[sample];
        if (!isfinite(value) || fwrite(&value, sizeof(float), 1, output) != 1)
            return fail("Cannot write finite mixed sample");
    }
    if (fclose(output)) return fail("Cannot finish rendered output");
    tsf_close(bank);
    free(mix);
    free(list);
    return 0;
}
