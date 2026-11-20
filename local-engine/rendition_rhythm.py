"""Write the recurring accent pattern of a new arrangement into a performer's part."""
import json


FIGURES = {
    "open": "a free or sustained texture, ordinary accompaniment, arpeggios, or no particular recurring pulse prescribed",
    "marcato": "strong alternating accents on two or four detached beats; melodic movement in the bass",
    "habanera": "long-short-long-long, a dotted eighth and sixteenth before two eighths",
    "tresillo": "repeating three-three-two subdivision against a steady pulse",
    "offbeat": "clipped chords between the steady beats with a separate bass groove",
    "swing": "long-short swinging subdivision with a walking or swinging rhythm section",
    "backbeat": "a pop or rock kick-and-snare backbeat",
    "waltz": "one strong beat followed by two lighter beats in three",
    "even": "a repeating regular per-beat pulse, on the beat with no asymmetrical grouping",
}


def choose_chart_figure(director, listener):
    return director.choose(
        "You are the band's timekeeper. Choose the characteristic rhythmic underpinning for the exact request. "
        "Choose an explicitly described playing pattern before inferring one from a genre. Choose open for an unspecified groove.",
        FIGURES, context=json.dumps({"listener": listener}))


def write_chart_figure(plan, figure, assigned_players):
    """Give one available pitched accompanist the accent pattern. Other players keep their complementary figures."""
    meter = plan["meter"]
    if meter not in (2, 4) or figure not in ("marcato", "habanera", "tresillo"):
        return
    role = next((r for kind in ("bass", "chords", "arpeggio") for r in plan["roles"]
                 if r["kind"] == kind and r["program"] not in assigned_players), None)
    if role is None:
        return
    if figure == "marcato":
        slots = [(i, .44, (0, 1, 2, 1)[i], 94 if i % 2 == 0 else 68) for i in range(meter)]
    else:
        # Positions are fractions of eight equal divisions of the bar. Gates end before the next attack.
        cell = ((0, 2.2, 0, 92), (3, .7, 2, 66), (4, 1.3, 1, 82), (6, 1.4, 2, 72)) if figure == "habanera" else (
            (0, 2., 0, 92), (3, 1.9, 2, 82), (6, 1.4, 1, 86))
        slots = [(start * meter / 8, length * meter / 8, degree, velocity)
                 for start, length, degree, velocity in cell]
    role["gesture"] = figure
    role["pattern"] = [{"beat": start, "duration": length, "degree": degree, "velocity": velocity}
                       for start, length, degree, velocity in slots]
    plan["subdivision"] = "straight"
