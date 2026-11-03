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
        if (!mode.equals("UNCONFIGURED") && !mode.equals("FIXTURE"))
            throw new IllegalArgumentException("DJOUND_MODE must be UNCONFIGURED or FIXTURE");
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

    public String adapterId() {
        return "scripted-samples-v1";
    }
}
