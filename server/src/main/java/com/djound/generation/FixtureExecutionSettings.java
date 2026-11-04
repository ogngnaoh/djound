package com.djound.generation;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.util.Set;

@Component
public record FixtureExecutionSettings(String checkpoint, String resultUrl) {
    @org.springframework.beans.factory.annotation.Autowired
    public FixtureExecutionSettings(
            @Value("${djound.fixture-checkpoint}") String checkpoint,
            @Value("${djound.fixture-result-url}") String resultUrl,
            GenerationConfiguration configuration) {
        this(checkpoint, resultUrl);
        if (!checkpoint.isEmpty()
                && !Set.of(
                                "BEFORE_SUBMIT",
                                "AFTER_SUBMITTING",
                                "AFTER_HANDLE",
                                "DURING_DOWNLOAD",
                                "AFTER_MEDIA_LINK",
                                "BEFORE_PUBLICATION_COMMIT",
                                "AFTER_PUBLICATION_COMMIT")
                        .contains(checkpoint))
            throw new IllegalArgumentException("Unknown fixture checkpoint");
        if ((!checkpoint.isEmpty() || !resultUrl.isEmpty()) && !configuration.fixture())
            throw new IllegalArgumentException("Fixture execution hooks require FIXTURE mode");
        if (!resultUrl.isEmpty()) validateUrl(resultUrl);
    }

    public static URI validateUrl(String value) {
        URI uri = URI.create(value);
        if (!"http".equals(uri.getScheme())
                || !Set.of("127.0.0.1", "[::1]", "::1").contains(uri.getHost())
                || uri.getPort() < 1
                || uri.getPort() > 65535
                || uri.getUserInfo() != null
                || uri.getFragment() != null)
            throw new IllegalArgumentException(
                    "Fixture result URL must be HTTP on a literal loopback address with an explicit"
                            + " port");
        return uri;
    }
}
