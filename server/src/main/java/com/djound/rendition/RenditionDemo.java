package com.djound.rendition;

import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import tools.jackson.databind.ObjectMapper;

import java.io.*;
import java.nio.file.*;
import java.util.UUID;

/** Opens the listening-room example as a normal, editable recording. */
@RestController
final class RenditionDemo {
    record Session(String projectId, String editId) {}

    private final JdbcClient db;
    private final TransactionTemplate transactions;
    private final RenditionAudio audio;

    RenditionDemo(JdbcClient db, TransactionTemplate transactions, RenditionAudio audio) {
        this.db = db;
        this.transactions = transactions;
        this.audio = audio;
    }

    @PostMapping("/api/renditions/demo")
    Session open() {
        return transactions.execute(
                status -> {
                    // Simultaneous visits share one example. Leave archived copies in Trash.
                    db.sql("SELECT id FROM workspace WHERE id=1 FOR UPDATE")
                            .query(Integer.class)
                            .single();
                    var existing =
                            db.sql(
                                            """
                                            SELECT p.id AS project_id, t.id AS edit_id FROM rendition_project p
                                            JOIN rendition_take t ON p.id=t.project_id
                                            WHERE p.import_key = 'djound-demo-v1/' || p.id AND t.request_id='example-jazz'
                                              AND p.trashed_at IS NULL AND t.trashed_at IS NULL
                                            ORDER BY p.created_at DESC LIMIT 1
                                            """)
                                    .query(Session.class)
                                    .optional();
                    if (existing.isPresent()) return existing.get();
                    var session =
                            new Session(UUID.randomUUID().toString(), UUID.randomUUID().toString());
                    try (var manifest =
                            new ClassPathResource("demo/evening-study.json").getInputStream()) {
                        var metadata = new ObjectMapper().readTree(manifest);
                        var original =
                                installAudio(
                                        session.projectId(),
                                        "original",
                                        metadata.path("sourceSha").asString());
                        var edit =
                                installAudio(
                                        session.editId(),
                                        "edit",
                                        metadata.path("editSha").asString());
                        var map =
                                RenditionTimeline.workerMap(
                                        metadata.path("parentTimeMap"),
                                        edit.frames(),
                                        original.frames(),
                                        false);
                        db.sql(
                                        """
                                        INSERT INTO rendition_project(id,title,import_key,upload_sha,source_sha,frames)
                                        VALUES(?, 'Evening Study', ?, ?, ?, ?)
                                        """)
                                .params(
                                        session.projectId(),
                                        "djound-demo-v1/" + session.projectId(),
                                        original.hash(),
                                        original.hash(),
                                        original.frames())
                                .update();
                        db.sql(
                                        """
                                        INSERT INTO rendition_take(id,project_id,request_id,prompt,state,label,caption,
                                            sha256,frames,song_time_map,arrangement_plan)
                                        VALUES(?,?,'example-jazz','Jazz trio','ready','Jazz trio',?,?,?,CAST(? AS jsonb),CAST(? AS jsonb))
                                        """)
                                .params(
                                        session.editId(),
                                        session.projectId(),
                                        metadata.path("caption").asString(),
                                        edit.hash(),
                                        edit.frames(),
                                        RenditionTimeline.encoded(map),
                                        RenditionTimeline.plan(metadata.path("arrangementPlan")))
                                .update();
                        return session;
                    } catch (IOException e) {
                        throw new UncheckedIOException("Could not open the packaged recording.", e);
                    }
                });
    }

    private RenditionAudio.Wave installAudio(String id, String name, String expectedHash)
            throws IOException {
        var target = audio.file(id);
        var staging = audio.temporary();
        try (var input = new ClassPathResource("static/media/" + name + ".wav").getInputStream()) {
            Files.copy(input, staging, StandardCopyOption.REPLACE_EXISTING);
            var wave = audio.verifyTake(staging);
            if (!wave.hash().equals(expectedHash))
                throw new IOException("Example audio checksum mismatch.");
            Files.move(staging, target, StandardCopyOption.ATOMIC_MOVE);
            TransactionSynchronizationManager.registerSynchronization(
                    new TransactionSynchronization() {
                        @Override
                        public void afterCompletion(int status) {
                            if (status == STATUS_ROLLED_BACK) {
                                try {
                                    Files.deleteIfExists(target);
                                } catch (IOException e) {
                                    throw new UncheckedIOException(e);
                                }
                            }
                        }
                    });
            return wave;
        } finally {
            Files.deleteIfExists(staging);
        }
    }
}
