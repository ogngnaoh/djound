package com.djound;

import java.util.List;

public class ApiProblem extends RuntimeException {
    final int status;
    final String code;
    final List<String> actions;

    public ApiProblem(int status, String code, String detail, String... actions) {
        super(detail);
        this.status = status;
        this.code = code;
        this.actions = List.of(actions);
    }
}
