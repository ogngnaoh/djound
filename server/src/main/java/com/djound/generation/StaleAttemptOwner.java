package com.djound.generation;

/** A former worker may no longer mutate, fail, or publish this attempt. */
public class StaleAttemptOwner extends RuntimeException {
    public StaleAttemptOwner() {
        super("Attempt ownership changed");
    }
}
