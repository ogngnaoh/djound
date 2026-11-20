package com.djound;

import jakarta.servlet.http.*;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.servlet.config.annotation.*;
import org.springframework.web.util.UriComponentsBuilder;

import java.util.*;

@Configuration
public class WebConfiguration implements WebMvcConfigurer {
    private final Set<String> origins;

    public WebConfiguration(@Value("${djound.allowed-origins}") String origins) {
        this.origins = Set.of(origins.split(","));
    }

    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(
                        new HandlerInterceptor() {
                            public boolean preHandle(
                                    HttpServletRequest request,
                                    HttpServletResponse response,
                                    Object handler) {
                                if (!Set.of("GET", "HEAD", "OPTIONS")
                                        .contains(request.getMethod())) {
                                    String origin = request.getHeader("Origin");
                                    if ((origin != null && !origins.contains(origin))
                                            || "cross-site"
                                                    .equals(request.getHeader("Sec-Fetch-Site")))
                                        throw new ApiProblem(
                                                403,
                                                "ORIGIN_REJECTED",
                                                "Open Djound from its local application address.",
                                                "OPEN_LOCAL_APP");
                                }
                                return true;
                            }
                        })
                .addPathPatterns("/api/**");
    }

    @Controller
    static class WebEntry {
        @GetMapping({"/renditions", "/tools/library"})
        String library() {
            return "redirect:/library.html";
        }

        @GetMapping("/renditions/{id}")
        String studio(@PathVariable UUID id, @RequestParam(required = false) String take) {
            var route = UriComponentsBuilder.fromPath("/studio.html").queryParam("project", id);
            if (take != null) route.queryParam("edit", "{edit}");
            return "redirect:" + route.encode().buildAndExpand(take == null ? "" : take).toUriString();
        }
    }
}
