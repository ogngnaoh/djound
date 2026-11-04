package com.djound.availability;

import org.springframework.stereotype.Component;

import java.util.concurrent.atomic.AtomicInteger;

/**
 * Non-generating local fixture observations. No HTTP client, provider endpoint, or adapter call.
 */
@Component
public class FixtureAvailabilityProbe {
    private final AtomicInteger checks = new AtomicInteger();

    public int checkCount() {
        return checks.get();
    }

    public String observe(String scenario) throws InterruptedException {
        checks.incrementAndGet();
        if (scenario.equals("STATUS_TIMEOUT")) Thread.sleep(5000);
        return scenario.equals("STATUS_SUCCESS") ? "ELIGIBLE" : "MALFORMED";
    }
}
