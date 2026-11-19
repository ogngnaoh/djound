/* v2: isolated track scheduling, shared upstream sample bank, explicit raw stems. */
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>
#define TSF_IMPLEMENTATION
#include "tsf.h"

#define MAX_EVENTS 16384
typedef struct { int frame, channel, on, pitch, velocity; } Event;

static int fail(const char *message) { fprintf(stderr, "%s\n", message); return 1; }

/* Locate the weighted quantile of a MIDI-note histogram. */
static int pitch_quantile(const long weights[128], long total, int percent) {
    long sum = 0;
    for (int key = 0; key < 128; ++key) {
        sum += weights[key];
        if (100 * sum >= percent * total) return key;
    }
    return 127;
}

static int range_distance(int pitch, int low, int high) {
    return pitch < low ? low - pitch : (pitch > high ? pitch - high : 0);
}

/* Play a narrow sampled instrument near its recorded timbre. A nominal SF2 key
   range can stretch a single sample across the keyboard; use unique sample roots
   and the duration of the actual guide notes rather than that nominal range. */
static int guide_octave(tsf *synth, int program, const Event *events, int count) {
    if (program < 8 || (program >= 80 && program < 104) || program >= 112) return 0;
    int index = tsf_get_presetindex(synth, 0, program);
    if (index < 0) return 0;
    long duration[128] = {0}, roots[128] = {0}, total = 0, root_count = 0;
    long velocities[128] = {0};
    int started[128], velocity[128];
    for (int k = 0; k < 128; ++k) started[k] = -1;
    for (int i = 0; i < count; ++i) {
        const Event *e = &events[i];
        if (e->on) {
            started[e->pitch] = e->frame;
            velocity[e->pitch] = e->velocity;
        } else if (started[e->pitch] >= 0) {
            int length = e->frame - started[e->pitch];
            duration[e->pitch] += length;
            velocities[velocity[e->pitch]] += length;
            total += length;
            started[e->pitch] = -1;
        }
    }
    if (!total) return 0;
    int dynamic = pitch_quantile(velocities, total, 50);
    int playable[128] = {0};
    const struct tsf_preset *preset = &synth->presets[index];
    for (int i = 0; i < preset->regionNum; ++i) {
        const struct tsf_region *region = &preset->regions[i];
        if (dynamic < region->lovel || dynamic > region->hivel) continue;
        int root = region->pitch_keycenter - region->transpose;
        if (root >= 0 && root < 128 && !roots[root]) {
            roots[root] = 1;
            ++root_count;
        }
        for (int k = region->lokey; k <= region->hikey && k < 128; ++k) playable[k] = 1;
    }
    if (root_count < 4) return 0;
    int span = pitch_quantile(roots, root_count, 100) - pitch_quantile(roots, root_count, 1);
    if (span < 9 || span > 55) return 0;
    int low = pitch_quantile(roots, root_count, 25), high = pitch_quantile(roots, root_count, 75);
    int melody = pitch_quantile(duration, total, 50);
    int shift = melody > high + 3 ? -12 : (melody < low - 3 ? 12 : 0);
    if (!shift) return 0;
    long improvement = 0;
    for (int k = 0; k < 128; ++k) if (duration[k]) {
        int target = k + shift;
        if (target < 0 || target > 127 || !playable[target]) return 0;
        improvement += duration[k] * (range_distance(k, low, high) - range_distance(target, low, high));
    }
    return improvement >= 3 * total ? shift : 0;
}

/* Stream one program on the source timeline. No song-length sample allocation. */
static int render_guide(const char *bank_path, const char *events_path, const char *output_path, int adapt) {
    FILE *events = fopen(events_path, "r");
    int frames, program, count;
    if (!events || fscanf(events, "%d %d %d", &frames, &program, &count) != 3 ||
        frames < 1 || frames > 300 * 44100 || program < 0 || program > 127 ||
        count < 2 || count > MAX_EVENTS) return fail("Invalid guide schedule");
    Event *notes = calloc((size_t)count, sizeof(Event));
    if (!notes) return fail("Cannot allocate guide events");
    for (int i = 0; i < count; ++i) {
        Event *e = &notes[i];
        if (fscanf(events, "%d %d %d %d", &e->frame, &e->on, &e->pitch, &e->velocity) != 4 ||
            e->frame < 0 || e->frame > frames || (i && e->frame < notes[i-1].frame) ||
            (e->on != 0 && e->on != 1) || e->pitch < 0 || e->pitch > 127 ||
            e->velocity < 1 || e->velocity > 127) return fail("Invalid guide event");
    }
    fclose(events);
    tsf *synth = tsf_load_filename(bank_path);
    if (!synth) return fail("Cannot load guide soundfont");
    tsf_set_output(synth, TSF_STEREO_INTERLEAVED, 44100, -6.0f);
    if (!tsf_set_max_voices(synth, 128) || !tsf_channel_set_bank_preset(synth, 0, 0, program))
        return fail("Guide instrument unavailable");
    FILE *out = fopen(output_path, "wbx");
    if (!out) return fail("Cannot open guide output");
    int position = 0;
    short buffer[1024 * 2];
    int shift = adapt ? guide_octave(synth, program, notes, count) : 0;
    for (int i = 0; i <= count; ++i) {
        int frame = i < count ? notes[i].frame : frames;
        while (position < frame) {
            int block = frame - position < 1024 ? frame - position : 1024;
            tsf_render_short(synth, buffer, block, 0);
            if (fwrite(buffer, 2 * sizeof(short), block, out) != (size_t)block)
                return fail("Cannot write guide samples");
            position += block;
        }
        if (i < count) {
            int pitch = notes[i].pitch + shift;
            if (pitch < 0 || pitch > 127) return fail("Invalid transposed guide pitch");
            if (notes[i].on && !tsf_channel_note_on(synth, 0, pitch, notes[i].velocity / 127.0f))
                return fail("Cannot play guide note");
            if (!notes[i].on) tsf_channel_note_off(synth, 0, pitch);
        }
    }
    free(notes);
    tsf_close(synth);
    return fclose(out) == 0 ? 0 : fail("Cannot finish guide");
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--guide-version") == 0) { puts("2"); return 0; }
    if (argc == 5 && strcmp(argv[1], "--guide") == 0) return render_guide(argv[2], argv[3], argv[4], 1);
    if (argc == 5 && strcmp(argv[1], "--guide-absolute") == 0) return render_guide(argv[2], argv[3], argv[4], 0);
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
