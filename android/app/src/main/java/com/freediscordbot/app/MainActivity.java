package com.freediscordbot.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;

/**
 * Free Discord Bot on Android: a remote control for the bot that runs on your PC (or server).
 *
 * 1. Pair: scan the QR code shown on the PC (Connection & Settings > Phone app), or type the address and code.
 * 2. Log in with Discord: only the account that owns the bot (or an allowed user) is accepted by the PC.
 * 3. The control panel opens, served by your PC through Tailscale. Keys are only shown as "set", never in full.
 *
 * The app also checks GitHub for a newer version of itself when it starts.
 */
public class MainActivity extends Activity {
    private static final String PREFS = "fdb";
    private static final String REPO = "Yeriangel/Free-Discord-Bot";
    private static final String REDIRECT = "freediscordbot://discord-callback";
    private static final String CONNECT_PAGE = "file:///android_asset/connect.html";

    private WebView web;
    private SharedPreferences prefs;
    private final Handler ui = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        web = new WebView(this);
        setContentView(web);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        web.addJavascriptInterface(new Bridge(), "Android");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                String base = prefs.getString("url", "");
                // the panel stays inside the app; other links (Discord, GitHub, docs) open in the browser
                if (u.toString().startsWith(CONNECT_PAGE) || (!base.isEmpty() && u.toString().startsWith(base))) return false;
                startActivity(new Intent(Intent.ACTION_VIEW, u));
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame() && !request.getUrl().toString().startsWith("file:")) showConnect("offline");
            }
        });
        if (!handleLink(getIntent())) start();
        checkForUpdate();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleLink(intent);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    /** Where to go when the app opens. */
    private void start() {
        String url = prefs.getString("url", ""), token = prefs.getString("token", "");
        if (url.isEmpty() || token.isEmpty()) showConnect("");
        else if (!prefs.getBoolean("verified", false)) discordLogin();
        else web.loadUrl(url + "/#token=" + Uri.encode(token));
    }

    private void showConnect(String status) {
        web.loadUrl(CONNECT_PAGE + "?status=" + Uri.encode(status) + "&paired=" + (prefs.getString("token", "").isEmpty() ? "0" : "1")
                + "&version=" + Uri.encode(versionName()));
    }

    /** freediscordbot://pair?... and freediscordbot://discord-callback#... */
    private boolean handleLink(Intent intent) {
        Uri u = intent == null ? null : intent.getData();
        if (u == null || !"freediscordbot".equals(u.getScheme())) return false;
        if ("pair".equals(u.getHost())) {
            pair(u.getQueryParameter("url"), u.getQueryParameter("code"));
            return true;
        }
        if ("discord-callback".equals(u.getHost())) {
            Uri fragment = Uri.parse("x://x?" + (u.getEncodedFragment() == null ? "" : u.getEncodedFragment()));
            String accessToken = fragment.getQueryParameter("access_token");
            String st = fragment.getQueryParameter("state");
            if (accessToken == null || st == null || !st.equals(prefs.getString("state", ""))) {
                showConnect("discord_failed");
            } else {
                finishDiscordLogin(accessToken);
            }
            return true;
        }
        return false;
    }

    private void pair(String url, String code) {
        if (url == null || code == null) { showConnect("bad_link"); return; }
        final String base = url.replaceAll("/+$", "");
        showConnect("pairing");
        new Thread(() -> {
            try {
                JSONObject body = new JSONObject().put("code", code.trim()).put("name", Build.MANUFACTURER + " " + Build.MODEL);
                JSONObject r = http("POST", base + "/api/mobile/claim", null, body);
                prefs.edit().putString("url", base).putString("token", r.getString("token"))
                        .putString("client_id", r.optString("client_id", "")).putBoolean("verified", false).apply();
                ui.post(this::discordLogin);
            } catch (Exception e) {
                ui.post(() -> showConnect("pair_failed:" + e.getMessage()));
            }
        }).start();
    }

    private void discordLogin() {
        String clientId = prefs.getString("client_id", "");
        if (clientId.isEmpty()) { showConnect("no_client_id"); return; }
        byte[] b = new byte[18];
        new SecureRandom().nextBytes(b);
        StringBuilder state = new StringBuilder();
        for (byte x : b) state.append(String.format("%02x", x));
        prefs.edit().putString("state", state.toString()).apply();
        Uri login = Uri.parse("https://discord.com/oauth2/authorize").buildUpon()
                .appendQueryParameter("client_id", clientId)
                .appendQueryParameter("response_type", "token")
                .appendQueryParameter("redirect_uri", REDIRECT)
                .appendQueryParameter("scope", "identify")
                .appendQueryParameter("state", state.toString()).build();
        showConnect("discord_waiting");
        startActivity(new Intent(Intent.ACTION_VIEW, login));
    }

    private void finishDiscordLogin(String accessToken) {
        String base = prefs.getString("url", ""), token = prefs.getString("token", "");
        showConnect("discord_checking");
        new Thread(() -> {
            try {
                http("POST", base + "/api/mobile/discord", token, new JSONObject().put("access_token", accessToken));
                prefs.edit().putBoolean("verified", true).apply();
                ui.post(this::start);
            } catch (Exception e) {
                ui.post(() -> showConnect("discord_refused:" + e.getMessage()));
            }
        }).start();
    }

    /** The app checks GitHub for a newer APK and offers it (Android asks before installing). */
    private void checkForUpdate() {
        new Thread(() -> {
            try {
                JSONObject rel = http("GET", "https://api.github.com/repos/" + REPO + "/releases/latest", null, null);
                String tag = rel.optString("tag_name", "").replaceFirst("^v", "");
                if (compare(tag, versionName()) <= 0) return;
                JSONArray assets = rel.optJSONArray("assets");
                String apk = null;
                for (int i = 0; assets != null && i < assets.length(); i++) {
                    JSONObject a = assets.getJSONObject(i);
                    if (a.optString("name").toLowerCase().endsWith(".apk")) apk = a.optString("browser_download_url");
                }
                if (apk == null) return;
                final String link = apk;
                ui.post(() -> new AlertDialog.Builder(this)
                        .setTitle("Update available")
                        .setMessage("Version " + tag + " is out (you have " + versionName() + "). Download it now?")
                        .setPositiveButton("Download", (d, w) -> startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(link))))
                        .setNegativeButton("Later", null).show());
            } catch (Exception ignored) {
                // no internet or GitHub not answering: try again next time
            }
        }).start();
    }

    private String versionName() {
        try {
            PackageInfo p = getPackageManager().getPackageInfo(getPackageName(), 0);
            return p.versionName == null ? "0" : p.versionName;
        } catch (Exception e) {
            return "0";
        }
    }

    static int compare(String a, String b) {
        String[] x = a.split("\\."), y = b.split("\\.");
        for (int i = 0; i < Math.max(x.length, y.length); i++) {
            int p = i < x.length ? parse(x[i]) : 0, q = i < y.length ? parse(y[i]) : 0;
            if (p != q) return Integer.compare(p, q);
        }
        return 0;
    }

    private static int parse(String s) {
        try { return Integer.parseInt(s.replaceAll("[^0-9]", "")); } catch (Exception e) { return 0; }
    }

    private static JSONObject http(String method, String url, String token, JSONObject body) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(10000);
        c.setReadTimeout(20000);
        c.setRequestProperty("Accept", "application/json");
        c.setRequestProperty("User-Agent", "FreeDiscordBot-Android");
        if (token != null) c.setRequestProperty("X-Session-Token", token);
        if (body != null) {
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json");
            try (OutputStream o = c.getOutputStream()) { o.write(body.toString().getBytes(StandardCharsets.UTF_8)); }
        }
        int code = c.getResponseCode();
        InputStream in = code < 400 ? c.getInputStream() : c.getErrorStream();
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        if (in != null) {
            byte[] chunk = new byte[8192];
            for (int n; (n = in.read(chunk)) > 0; ) buf.write(chunk, 0, n);
        }
        String text = buf.toString("UTF-8");
        if (code >= 400) {
            String msg = text;
            try { msg = new JSONObject(text).getJSONObject("detail").optString("message", text); } catch (Exception ignored) { }
            throw new Exception(msg.isEmpty() ? "HTTP " + code : msg);
        }
        return text.isEmpty() ? new JSONObject() : new JSONObject(text);
    }

    /** Called from connect.html. */
    private class Bridge {
        @JavascriptInterface
        public void pair(String url, String code) { ui.post(() -> MainActivity.this.pair(url, code)); }

        @JavascriptInterface
        public void retry() { ui.post(MainActivity.this::start); }

        @JavascriptInterface
        public void login() { ui.post(MainActivity.this::discordLogin); }

        @JavascriptInterface
        public void forget() {
            prefs.edit().clear().apply();
            ui.post(() -> showConnect(""));
        }
    }
}
