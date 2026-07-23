package com.example;

import java.util.List;
import org.springframework.stereotype.Component;

/**
 * Main application class.
 * This should be minified natively by Go.
 */
@Component
public class App {
    private final String name;

    public App(String name) {
        this.name = name;
        System.out.println("Initializing App: " + name);
    }

    public String getName() {
        return this.name;
    }

    public static void main(String[] args) {
        System.out.println("Running application...");
        App app = new App("CIDA Motor");
    }
}
