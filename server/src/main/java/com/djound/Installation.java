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
        } catch (IOException e) {
            try {
                channel.close();
            } catch (IOException cleanup) {
                e.addSuppressed(cleanup);
            }
            throw e;
        }
        if (acquired == null) {
            channel.close();
            throw new IllegalStateException("Djound data directory is already in use");
        }
        lock = acquired;
        try {
            JdbcClient jdbc = JdbcClient.create(ds);
            Path identity = root.resolve("installation.id");
            // An existing pairing must be checked before Flyway can modify that database.
            // Flyway migrates the connection's current schema; a fallback schema is not this
            // installation.
            if (Files.exists(identity)) UUID.fromString(Files.readString(identity).trim());
            if (jdbc.sql("SELECT to_regclass(format('%I.workspace', current_schema())) IS NOT NULL")
                    .query(Boolean.class)
                    .single()) {
                var existing =
                        jdbc.sql(
                                        "SELECT installation_id,canonical_directory FROM workspace"
                                                + " WHERE id=1")
                                .query()
                                .listOfRows();
                if (!existing.isEmpty()) {
                    var binding = existing.getFirst();
                    if (!root.toString().equals(binding.get("canonical_directory"))
                            || !Files.exists(identity)
                            || !Files.readString(identity)
                                    .trim()
                                    .equals(binding.get("installation_id")))
                        throw new IllegalStateException(
                                "Database is bound to another Djound installation/data directory,"
                                        + " or its installation identity is missing");
                }
            }
            Flyway.configure().dataSource(ds).load().migrate();
            if (!Files.exists(identity))
                Files.writeString(
                        identity, UUID.randomUUID().toString(), StandardOpenOption.CREATE_NEW);
            String id = Files.readString(identity).trim();
            UUID.fromString(id);
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
        } catch (Exception e) {
            try {
                close();
            } catch (IOException cleanup) {
                e.addSuppressed(cleanup);
            }
            throw e;
        }
    }

    public Path rootPath() {
        return root;
    }

    public void close() throws IOException {
        if (lock.isValid()) lock.release();
        if (channel.isOpen()) channel.close();
    }
}
