package com.djound.availability;

import com.djound.generation.GenerationConfiguration;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.context.annotation.*;

import java.time.Clock;

@Configuration
public class AvailabilityConfiguration {
    @Bean
    @ConditionalOnMissingBean(Clock.class)
    public Clock availabilityClock() {
        return Clock.systemUTC();
    }

    @Bean
    public Cooldown availabilityCooldown(
            @Value("${djound.fixture-cooldown-ms}") String override,
            GenerationConfiguration configuration) {
        if (override.isEmpty()) return new Cooldown(60000);
        if (!configuration.fixture())
            throw new IllegalArgumentException("DJOUND_FIXTURE_COOLDOWN_MS requires FIXTURE mode");
        long value = Long.parseLong(override);
        if (value < 1 || value > 600000)
            throw new IllegalArgumentException(
                    "Fixture cooldown must be between 1 and 600000 milliseconds");
        return new Cooldown(value);
    }

    public record Cooldown(long millis) {}
}
