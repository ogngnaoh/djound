package com.djound.generation;

import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

import java.io.*;
import java.nio.file.Files;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Predefined bundled audio, independent of source bytes or instructions. No network client exists.
 */
@Component
public class ScriptedAdapter {
    private final GenerationConfiguration configuration;
    private final AtomicInteger executions = new AtomicInteger();

    public ScriptedAdapter(GenerationConfiguration configuration) {
        this.configuration = configuration;
    }

    public int executionCount() {
        return executions.get();
    }

    public MultipartFile execute(long ordinal) throws InterruptedException {
        if (!configuration.fixture())
            throw new IllegalStateException("Fixture execution is disabled");
        executions.incrementAndGet();
        Thread.sleep(configuration.delayMs());
        String name = ordinal % 2 == 0 ? "sample-warm.wav" : "sample-pulse.wav";
        return new FixtureFile(new ClassPathResource("fixtures/audio/" + name), name);
    }

    private record FixtureFile(ClassPathResource resource, String filename)
            implements MultipartFile {
        public String getName() {
            return "file";
        }

        public String getOriginalFilename() {
            return filename;
        }

        public String getContentType() {
            return "audio/wav";
        }

        public boolean isEmpty() {
            return false;
        }

        public long getSize() {
            try {
                return resource.contentLength();
            } catch (IOException error) {
                throw new UncheckedIOException(error);
            }
        }

        public byte[] getBytes() throws IOException {
            return resource.getContentAsByteArray();
        }

        public InputStream getInputStream() throws IOException {
            return resource.getInputStream();
        }

        public void transferTo(File destination) throws IOException {
            try (var in = getInputStream()) {
                Files.copy(in, destination.toPath());
            }
        }
    }
}
