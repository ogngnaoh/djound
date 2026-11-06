package com.djound.generation;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public record GenerationConfiguration(String mode, long delayMs, boolean reviewControls) {
    @org.springframework.beans.factory.annotation.Autowired
    public GenerationConfiguration(
            @Value("${djound.mode}") String mode,
            @Value("${djound.fixture-delay-ms}") long delayMs,
            @Value("${djound.fixture-review-controls}") boolean reviewControls) {
        if (mode.equals("LIVE"))
            throw new IllegalArgumentException(
                    "DJOUND_MODE=LIVE is retired. Use LOCAL to create music or UNCONFIGURED to play"
                        + " saved recordings.");
        if (!mode.equals("UNCONFIGURED") && !mode.equals("FIXTURE") && !mode.equals("LOCAL"))
            throw new IllegalArgumentException(
                    "DJOUND_MODE must be UNCONFIGURED, FIXTURE, or LOCAL");
        if (delayMs < 0 || delayMs > 30000)
            throw new IllegalArgumentException(
                    "DJOUND_FIXTURE_DELAY_MS must be between 0 and 30000");
        this.mode = mode;
        this.delayMs = delayMs;
        this.reviewControls = reviewControls;
    }

    public GenerationConfiguration(String mode, long delayMs) {
        this(mode, delayMs, false);
    }

    public boolean controlsEnabled() {
        return fixture() && reviewControls;
    }

    public boolean fixture() {
        return mode.equals("FIXTURE");
    }

    public boolean live() {
        return mode.equals("LIVE");
    }

    public boolean local() {
        return mode.equals("LOCAL");
    }

    public boolean configured() {
        return fixture() || live() || local();
    }

    public String adapterId() {
        return local()
                ? "local-notes-v1"
                : live() ? "melodyflow-anonymous-v1" : "scripted-samples-v1";
    }
}
