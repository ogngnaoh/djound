package com.djound.rendition;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.util.*;

/** Frame landmarks relate a performed take to the same musical position in its source. */
final class RenditionTimeline {
    private static final ObjectMapper json = new ObjectMapper();
    static final int MAX_WORKER_POINTS = 4096;
    private static final int MAX_STORED_POINTS = 16384;

    static int[][] storedMap(String value) {
        return value == null ? null : json.readValue(value, int[][].class);
    }

    static String encoded(Object value) {
        return value == null ? null : json.writeValueAsString(value);
    }

    static JsonNode storedPlan(String value) {
        return value == null ? null : json.readTree(value);
    }

    static String plan(JsonNode value) throws IOException {
        if (value.isMissingNode() || value.isNull()) return null;
        var version = value.path("version");
        if (!value.isObject()
                || !version.isIntegralNumber()
                || !version.canConvertToInt()
                || version.asInt() != 1
                || json.writeValueAsBytes(value).length >= 48 * 1024) {
            throw new IOException("The worker returned an invalid arrangement plan.");
        }
        validatePlanNode(value, 0);
        return encoded(value);
    }

    private static void validatePlanNode(JsonNode node, int depth) throws IOException {
        if (depth > 12
                || node.isNumber() && !Double.isFinite(node.asDouble())
                || node.isString() && node.asString().chars().anyMatch(Character::isISOControl)) {
            throw new IOException("The arrangement plan contains an invalid value.");
        }
        if (node.isObject()) {
            for (String key : node.propertyNames()) {
                if (key.isEmpty()
                        || key.length() > 80
                        || key.chars().anyMatch(Character::isISOControl))
                    throw new IOException("The arrangement plan contains an invalid key.");
            }
        }
        for (JsonNode child : node) validatePlanNode(child, depth + 1);
    }

    static int[][] workerMap(JsonNode value, int outputFrames, int parentFrames, boolean scoped)
            throws IOException {
        if (scoped && outputFrames != parentFrames)
            throw new IOException("A passage edit changed the length of its parent.");
        if (value.isMissingNode() || value.isNull()) {
            if (outputFrames != parentFrames)
                throw new IOException("A different-length performance requires a song time map.");
            return null;
        }
        if (!value.isArray() || value.size() < 2 || value.size() > MAX_WORKER_POINTS)
            throw new IOException("The worker returned an invalid song time map.");
        int[][] points = new int[value.size()][2];
        int i = 0;
        boolean identity = true;
        for (JsonNode knot : value) {
            if (!knot.isArray() || knot.size() != 2)
                throw new IOException("Each song time landmark needs two frame positions.");
            int x = frame(knot.get(0), outputFrames), y = frame(knot.get(1), parentFrames);
            if (i == 0 && (x != 0 || y != 0)
                    || i > 0 && (x <= points[i - 1][0] || y < points[i - 1][1]))
                throw new IOException(
                        "The song time landmarks must advance from the start of the recording.");
            points[i++] = new int[] {x, y};
            identity &= x == y;
        }
        if (points[i - 1][0] != outputFrames || points[i - 1][1] != parentFrames)
            throw new IOException("The song time map must cover both complete recordings.");
        if (scoped && !identity)
            throw new IOException("A passage edit changed its parent's song timing.");
        return identity ? null : points;
    }

    private static int frame(JsonNode node, int limit) throws IOException {
        if (!node.isIntegralNumber()
                || !node.canConvertToInt()
                || node.asInt() < 0
                || node.asInt() > limit)
            throw new IOException("The song time map contains an out-of-range frame position.");
        return node.asInt();
    }

    private static double valueAt(int[][] points, double x) {
        int low = 0, high = points.length - 1;
        while (high - low > 1) {
            int mid = (low + high) >>> 1;
            if (points[mid][0] <= x) low = mid;
            else high = mid;
        }
        int[] a = points[low], b = points[high];
        return a[1] + (x - a[0]) * (b[1] - a[1]) / (b[0] - a[0]);
    }

    /** Compose child→parent and parent→upload; include tempo changes from both performances. */
    static int[][] toOriginal(int[][] edge, int[][] parent) throws IOException {
        if (edge == null) return parent;
        if (parent == null) return edge;
        var times = new TreeSet<Integer>();
        for (int[] point : edge) times.add(point[0]);
        int i = 0;
        for (int[] point : parent) {
            while (i + 1 < edge.length - 1 && edge[i + 1][1] < point[0]) i++;
            int[] a = edge[i], b = edge[i + 1];
            if (a[1] < point[0] && point[0] < b[1]) {
                double fraction = (point[0] - a[1]) / (double) (b[1] - a[1]);
                times.add(a[0] + (int) Math.round(fraction * (b[0] - a[0])));
            }
        }
        var where = new ArrayList<int[]>();
        for (int x : times) {
            int y = (int) Math.round(valueAt(parent, valueAt(edge, x)));
            while (where.size() >= 2) {
                int[] a = where.get(where.size() - 2), b = where.getLast();
                if ((long) (b[1] - a[1]) * (x - b[0]) != (long) (y - b[1]) * (b[0] - a[0])) break;
                where.removeLast();
            }
            where.add(new int[] {x, y});
        }
        if (where.size() > MAX_STORED_POINTS)
            throw new IOException("The combined song time map contains too many landmarks.");
        if (where.stream().allMatch(p -> p[0] == p[1])) return null;
        return where.toArray(int[][]::new);
    }
}
