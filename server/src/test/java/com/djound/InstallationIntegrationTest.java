package com.djound;

import static org.junit.jupiter.api.Assertions.*;

import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

import java.nio.channels.FileChannel;
import java.nio.file.*;
import java.sql.DriverManager;
import java.util.UUID;

/** Startup failures must not migrate or rebind an existing installation. Real PostgreSQL only. */
class InstallationIntegrationTest {
    @TempDir Path root;
    String schema;
    DriverManagerDataSource dataSource;
    JdbcClient jdbc;

    @BeforeEach
    void createSchema() throws Exception {
        schema = "installation_" + UUID.randomUUID().toString().replace("-", "");
        try (var connection =
                        DriverManager.getConnection(
                                BackendIntegrationTest.BASE,
                                BackendIntegrationTest.USER,
                                BackendIntegrationTest.PASSWORD);
                var statement = connection.createStatement()) {
            statement.execute("CREATE SCHEMA " + schema);
        }
        dataSource =
                new DriverManagerDataSource(
                        BackendIntegrationTest.BASE
                                + (BackendIntegrationTest.BASE.contains("?") ? "&" : "?")
                                + "currentSchema="
                                + schema,
                        BackendIntegrationTest.USER,
                        BackendIntegrationTest.PASSWORD);
        jdbc = JdbcClient.create(dataSource);
    }

    @AfterEach
    void dropSchema() throws Exception {
        try (var connection =
                        DriverManager.getConnection(
                                BackendIntegrationTest.BASE,
                                BackendIntegrationTest.USER,
                                BackendIntegrationTest.PASSWORD);
                var statement = connection.createStatement()) {
            statement.execute("DROP SCHEMA " + schema + " CASCADE");
        }
    }

    String version() {
        return jdbc.sql(
                        "SELECT version FROM flyway_schema_history WHERE success AND version IS NOT"
                                + " NULL ORDER BY installed_rank DESC LIMIT 1")
                .query(String.class)
                .single();
    }

    Path bindVersionOne() throws Exception {
        Flyway.configure().dataSource(dataSource).target("1").load().migrate();
        Path original = Files.createDirectory(root.resolve("original")).toRealPath();
        String identity = UUID.randomUUID().toString();
        Files.writeString(original.resolve("installation.id"), identity);
        jdbc.sql("INSERT INTO workspace(id,installation_id,canonical_directory) VALUES(1,?,?)")
                .params(identity, original.toString())
                .update();
        return original;
    }

    @Test
    void wrongDirectoryCannotApplyPendingMigrationsEvenWithCopiedIdentity() throws Exception {
        Path original = bindVersionOne();
        Path wrong = Files.createDirectory(root.resolve("wrong"));
        assertThrows(
                IllegalStateException.class, () -> new Installation(dataSource, wrong.toString()));
        assertEquals("1", version());
        assertFalse(
                Files.exists(wrong.resolve("installation.id")),
                "Rejected startup must not manufacture an identity");
        Files.copy(original.resolve("installation.id"), wrong.resolve("installation.id"));
        assertThrows(
                IllegalStateException.class, () -> new Installation(dataSource, wrong.toString()));
        assertEquals("1", version());
        assertEquals(
                original.toString(),
                jdbc.sql("SELECT canonical_directory FROM workspace WHERE id=1")
                        .query(String.class)
                        .single());
        // Constructor failure must release the OS lock rather than preventing a corrected launch.
        try (var channel =
                        FileChannel.open(
                                wrong.resolve("installation.lock"), StandardOpenOption.WRITE);
                var lock = channel.tryLock()) {
            assertNotNull(lock);
        }
        try (var installation = new Installation(dataSource, original.toString())) {
            assertNotEquals("1", version());
            assertTrue(Files.isDirectory(installation.rootPath().resolve("media")));
        }
    }

    @Test
    void missingIdentityDoesNotRegenerateOrMigrateBoundDirectory() throws Exception {
        Path original = bindVersionOne();
        Files.delete(original.resolve("installation.id"));
        assertThrows(
                IllegalStateException.class,
                () -> new Installation(dataSource, original.toString()));
        assertEquals("1", version());
        assertFalse(Files.exists(original.resolve("installation.id")));
    }

    @Test
    void freshSchemaInitializesAndSameDirectoryRestarts() throws Exception {
        Path directory = root.resolve("fresh");
        String identity;
        try (var installation = new Installation(dataSource, directory.toString())) {
            identity = Files.readString(directory.resolve("installation.id"));
            assertEquals(
                    identity,
                    jdbc.sql("SELECT installation_id FROM workspace WHERE id=1")
                            .query(String.class)
                            .single());
            assertThrows(
                    IllegalStateException.class,
                    () -> new Installation(dataSource, directory.toString()));
        }
        try (var installation = new Installation(dataSource, directory.toString())) {
            assertEquals(identity, Files.readString(directory.resolve("installation.id")));
        }
    }

    @Test
    void unrelatedFallbackSchemaDoesNotPreventFreshInitialization() throws Exception {
        String fallback = schema + "_fallback";
        jdbc.sql("CREATE SCHEMA " + fallback).update();
        try {
            jdbc.sql(
                            "CREATE TABLE "
                                    + fallback
                                    + ".workspace(id integer,installation_id"
                                    + " text,canonical_directory text)")
                    .update();
            jdbc.sql("INSERT INTO " + fallback + ".workspace VALUES(1,?,'unrelated-directory')")
                    .param(UUID.randomUUID().toString())
                    .update();
            var withFallback =
                    new DriverManagerDataSource(
                            dataSource.getUrl() + "," + fallback,
                            BackendIntegrationTest.USER,
                            BackendIntegrationTest.PASSWORD);
            try (var installation =
                    new Installation(withFallback, root.resolve("fresh").toString())) {
                assertEquals(
                        installation.rootPath().toString(),
                        jdbc.sql("SELECT canonical_directory FROM workspace WHERE id=1")
                                .query(String.class)
                                .single());
                assertEquals(
                        "unrelated-directory",
                        jdbc.sql(
                                        "SELECT canonical_directory FROM "
                                                + fallback
                                                + ".workspace WHERE id=1")
                                .query(String.class)
                                .single());
            }
        } finally {
            jdbc.sql("DROP SCHEMA " + fallback + " CASCADE").update();
        }
    }
}
