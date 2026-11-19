package com.djound.mix;

import static com.djound.mix.MixModels.*;

import com.djound.ApiProblem;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
class MixStore {
    static final String ORIGINAL = "original";
    final JdbcClient db;

    MixStore(JdbcClient db, MixArtifacts initialized) {
        this.db = db;
    }

    record Project(
            String id,
            String title,
            String importSha,
            int durationMs,
            Integer frames,
            String sourcePath,
            String sourceSha,
            String preparation,
            String error,
            String jobId,
            String passageId,
            String heardTake,
            String keptTake,
            long revision) {}

    record Passage(
            String id,
            String projectId,
            String baseId,
            int startFrame,
            int endFrame,
            String prepPath,
            String prepSha,
            String cacheKey) {}

    record SavedTake(
            String id,
            int number,
            String projectId,
            String passageId,
            String parentId,
            String referenceId,
            String baseId,
            int startFrame,
            int endFrame,
            double vocalsDb,
            double drumsDb,
            double passageTrimDb,
            String lastControl,
            double lastChangeDb,
            String requestText,
            String label,
            String audioPath,
            String sha256,
            boolean heard,
            boolean kept) {
        Take view() {
            return new Take(
                    id,
                    number,
                    parentId,
                    referenceId,
                    baseId,
                    startFrame,
                    endFrame,
                    vocalsDb,
                    drumsDb,
                    passageTrimDb,
                    lastControl,
                    lastChangeDb,
                    lastChangeDb > 0 ? "up" : lastChangeDb < 0 ? "down" : "unchanged",
                    label,
                    requestText,
                    url(projectId, id),
                    sha256,
                    kept,
                    heard);
        }

        double gain(String control) {
            return "vocals".equals(control) ? vocalsDb : drumsDb;
        }
    }

    private static final String TAKE_JOIN =
            """
            SELECT t.*,p.base_id,p.start_frame,p.end_frame FROM mix_take t
            JOIN mix_passage p ON p.id=t.passage_id AND p.project_id=t.project_id
            WHERE t.project_id=?
            """;

    static String url(String id, String take) {
        return "/api/mixes/" + id + "/takes/" + take + "/audio";
    }

    Project project(String id, boolean lock) {
        return db.sql("SELECT * FROM mix_project WHERE id=?" + (lock ? " FOR UPDATE" : ""))
                .param(id)
                .query(Project.class)
                .optional()
                .orElseThrow(
                        () ->
                                new ApiProblem(
                                        404,
                                        "MIX_NOT_FOUND",
                                        "This finished mix could not be found.",
                                        "RETURN_TO_LIBRARY"));
    }

    Passage passage(String project, String id) {
        if (id == null) return null;
        return db.sql("SELECT * FROM mix_passage WHERE project_id=? AND id=?")
                .params(project, id)
                .query(Passage.class)
                .single();
    }

    List<SavedTake> takes(String id) {
        return db.sql(TAKE_JOIN + " ORDER BY t.number").param(id).query(SavedTake.class).list();
    }

    SavedTake take(String project, String id) {
        return db.sql(TAKE_JOIN + " AND t.id=?")
                .params(project, id)
                .query(SavedTake.class)
                .optional()
                .orElseThrow(
                        () ->
                                new ApiProblem(
                                        404,
                                        "MIX_TAKE_NOT_FOUND",
                                        "This take does not belong to this mix.",
                                        "REFRESH"));
    }

    State state(Project p) {
        Passage s = passage(p.id(), p.passageId());
        return new State(
                p.id(),
                p.title(),
                MixArtifacts.RATE,
                p.frames() == null ? 0 : p.frames(),
                p.durationMs(),
                "/api/mixes/" + p.id() + "/import",
                p.sourcePath() == null ? null : url(p.id(), ORIGINAL),
                p.revision(),
                s == null ? null : new Selection(s.startFrame(), s.endFrame()),
                s == null ? ORIGINAL : s.baseId(),
                p.preparation(),
                p.error(),
                p.heardTake(),
                p.keptTake(),
                takes(p.id()).stream().map(SavedTake::view).toList());
    }
}
