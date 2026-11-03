package com.djound;

import org.flywaydb.core.Flyway;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.channels.*;
import java.nio.file.*;
import java.util.UUID;

import javax.sql.DataSource;

/**
 * Lifetime OS lock is acquired before any migrations. A database cannot be rebound to another
 * directory.
 */
@Component
public class Installation implements AutoCloseable {
    final Path root;
    private final FileChannel channel;
    private final FileLock lock;

    public Installation(DataSource ds, @Value("${djound.data-dir}") String directory)
            throws IOException {
        Files.createDirectories(Path.of(directory));
        root = Path.of(directory).toRealPath();
        channel =
                FileChannel.open(
                        root.resolve("installation.lock"),
                        StandardOpenOption.CREATE,
                        StandardOpenOption.WRITE);
        FileLock acquired;
        try {
            acquired = channel.tryLock();
        } catch (OverlappingFileLockException e) {
            channel.close();
            throw new IllegalStateException("Djound data directory is already in use", e);
        }
        if (acquired == null) {
            channel.close();
            throw new IllegalStateException("Djound data directory is already in use");
        }
        lock = acquired;
        try {
            Flyway.configure().dataSource(ds).load().migrate();
            Path identity = root.resolve("installation.id");
            if (!Files.exists(identity))
                Files.writeString(
                        identity, UUID.randomUUID().toString(), StandardOpenOption.CREATE_NEW);
            String id = Files.readString(identity).trim();
            UUID.fromString(id);
            JdbcClient jdbc = JdbcClient.create(ds);
            jdbc.sql(
                            "INSERT INTO workspace(id,installation_id,canonical_directory)"
                                    + " VALUES(1,?,?) ON CONFLICT DO NOTHING")
                    .params(id, root.toString())
                    .update();
            var row =
                    jdbc.sql("SELECT installation_id,canonical_directory FROM workspace WHERE id=1")
                            .query()
                            .singleRow();
            if (!id.equals(row.get("installation_id"))
                    || !root.toString().equals(row.get("canonical_directory")))
                throw new IllegalStateException(
                        "Database is bound to another Djound installation/data directory");
            Files.createDirectories(root.resolve("media"));
            Files.createDirectories(root.resolve("temporary"));
            // No requests run during startup; all these files are unpublished uploads from a prior
            // process.
            try (var paths = Files.list(root.resolve("temporary"))) {
                for (Path p : paths.toList()) if (Files.isRegularFile(p)) Files.delete(p);
            }
        } catch (Exception e) {
            close();
            throw e;
        }
    }

    public void close() throws IOException {
        if (lock.isValid()) lock.release();
        if (channel.isOpen()) channel.close();
    }
}
