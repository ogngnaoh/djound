package com.djound;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.*;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MissingRequestValueException;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.multipart.MultipartException;
import org.springframework.web.servlet.resource.NoResourceFoundException;

import java.util.*;

@RestControllerAdvice
public class ProblemHandler {
    private static final Logger log = LoggerFactory.getLogger(ProblemHandler.class);

    @ExceptionHandler(ApiProblem.class)
    ResponseEntity<Map<String, Object>> problem(ApiProblem e) {
        return body(e.status, e.code, e.getMessage(), e.actions);
    }

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    ResponseEntity<Map<String, Object>> large(Exception e) {
        return body(
                413,
                "UPLOAD_TOO_LARGE",
                "Choose an audio file no larger than 64 MiB.",
                List.of("CHOOSE_FILE"));
    }

    @ExceptionHandler({
        MissingRequestValueException.class,
        MultipartException.class,
        IllegalArgumentException.class
    })
    ResponseEntity<Map<String, Object>> malformed(Exception e) {
        return body(
                400,
                "INVALID_REQUEST",
                "The request is incomplete or invalid.",
                List.of("RETRY_UPLOAD"));
    }

    @ExceptionHandler(NoResourceFoundException.class)
    ResponseEntity<Map<String, Object>> missing(Exception e) {
        return body(
                404,
                "NOT_FOUND",
                "This resource could not be found.",
                List.of("RETURN_TO_LIBRARY"));
    }

    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    ResponseEntity<Map<String, Object>> method(Exception e) {
        return body(405, "METHOD_NOT_ALLOWED", "This action is not supported.", List.of());
    }

    @ExceptionHandler(Exception.class)
    ResponseEntity<Map<String, Object>> internal(Exception e) {
        log.error("Request failed", e);
        return body(
                500,
                "INTERNAL_ERROR",
                "Djound could not complete this action. Your saved music is unchanged.",
                List.of("RETRY"));
    }

    static ResponseEntity<Map<String, Object>> body(
            int status, String code, String detail, List<String> actions) {
        return ResponseEntity.status(status)
                .contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .body(
                        Map.of(
                                "type",
                                "urn:djound:problem:" + code.toLowerCase(Locale.ROOT),
                                "title",
                                code.replace('_', ' '),
                                "status",
                                status,
                                "detail",
                                detail,
                                "code",
                                code,
                                "permittedActions",
                                actions));
    }
}
