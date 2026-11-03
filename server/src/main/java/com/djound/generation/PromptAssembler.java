package com.djound.generation;

import com.djound.ApiProblem;

/**
 * Literal composition only. It makes no claim about a model understanding or transforming music.
 */
public final class PromptAssembler {
    public static final String VERSION = "literal-v1";

    public record Assembled(String direction, String instructions) {}

    public Assembled assemble(String inherited, String submitted) {
        if (submitted == null || submitted.isBlank())
            throw new ApiProblem(
                    422,
                    "DIRECTION_REQUIRED",
                    "Describe the version you want to hear.",
                    "EDIT_TEXT");
        if (submitted.codePointCount(0, submitted.length()) > 4000)
            throw new ApiProblem(
                    422,
                    "TEXT_TOO_LONG",
                    "Direction must be at most 4,000 characters.",
                    "EDIT_TEXT");
        String direction =
                inherited.isEmpty() ? submitted : inherited + "\n\nLatest feedback:\n" + submitted;
        String instructions =
                "Create an instrumental interpretation using the original source recording.\n"
                        + "Preserve a recognizable connection to its melody.\n"
                        + "Inherited direction (literal):\n"
                        + inherited
                        + "\n\nLatest direction (literal, takes priority):\n"
                        + submitted;
        if (instructions.codePointCount(0, instructions.length()) > 16000)
            throw new ApiProblem(
                    422,
                    "ASSEMBLED_DIRECTION_TOO_LONG",
                    "This direction and its inherited context exceed 16,000 characters. Choose the"
                            + " original as the basis and shorten the direction.",
                    "EDIT_TEXT");
        return new Assembled(direction, instructions);
    }
}
