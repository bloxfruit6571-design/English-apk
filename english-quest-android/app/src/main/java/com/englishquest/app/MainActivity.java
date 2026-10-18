package com.englishquest.app;

import android.Manifest;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.os.StatFs;
import android.provider.Settings;
import android.speech.RecognizerIntent;
import android.speech.RecognitionListener;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.speech.tts.Voice;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.core.view.WindowCompat;
import androidx.webkit.WebViewAssetLoader;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Locale;
import java.util.Set;
import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

public final class MainActivity extends Activity {
    private static final String APP_ORIGIN = "https://appassets.androidplatform.net";
    private static final int REQUEST_MIC_PERMISSION = 301;
    private static final String PROFILE_PREFERENCES = "english_quest_profile";
    private static final String PROFILE_JSON_KEY = "profile_json";
    private static final String VOSK_MODEL_ARCHIVE = "vosk-model-small-en-us-0.15.zip";
    private static final String VOSK_MODEL_ROOT = "vosk-model-small-en-us-0.15/";
    private static final long MIN_MODEL_FREE_BYTES = 85L * 1024L * 1024L;
    private static final long MIN_EXTRACTED_MODEL_BYTES = 60_000_000L;
    private static final int VOSK_SAMPLE_RATE = 16_000;
    private static final int VOSK_AUDIO_BUFFER_SAMPLES = 3_200;
    private static final int MAX_RENDERER_RESTARTS = 2;
    // Static on purpose: it must survive Activity.recreate() so a WebView renderer that keeps
    // dying cannot restart the UI in a loop. Reset whenever a page finishes loading normally.
    private static int rendererRestartCount = 0;
    private WebView webView;
    private WebViewAssetLoader assetLoader;
    private TextToSpeech textToSpeech;
    private Voice defaultEnglishVoice;
    private volatile boolean ttsReady = false;
    private volatile boolean englishMaleVoiceAvailable = false;
    private volatile boolean englishFemaleVoiceAvailable = false;
    private volatile String pendingSpeechId = "";
    private String pendingSpeechTarget = "";
    private volatile boolean speechRequestPending = false;
    private boolean pendingOnlineFallbackAllowed = false;
    private boolean pendingOnlineAttempted = false;
    private boolean pendingForceOnline = false;
    private boolean pendingUsingOnlineRecognizer = false;
    private String pendingOfflineFailureMessage = "";
    private volatile int speechAttemptSerial = 0;
    private SpeechRecognizer speechRecognizer;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService voskExecutor = Executors.newSingleThreadExecutor();
    private volatile org.vosk.Model voskModel;
    private volatile boolean voskModelLoading = false;
    private volatile org.vosk.Recognizer offlineRecognizer;
    private volatile AudioRecord offlineRecorder;
    private volatile Thread offlineAudioThread;
    private volatile boolean offlineCaptureRunning = false;
    private volatile boolean offlineCollectFinalResult = false;
    private Runnable offlineSpeechTimeout;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        WindowCompat.setDecorFitsSystemWindows(getWindow(), true);
        getWindow().setStatusBarColor(Color.rgb(238, 244, 255));
        getWindow().setNavigationBarColor(Color.rgb(246, 248, 253));
        int systemUi = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            systemUi |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
        }
        getWindow().getDecorView().setSystemUiVisibility(systemUi);

        assetLoader = new WebViewAssetLoader.Builder()
                .addPathHandler("/assets/", new WebViewAssetLoader.AssetsPathHandler(this))
                .build();

        webView = new WebView(this);
        webView.setBackgroundColor(Color.rgb(246, 248, 253));
        webView.setOverScrollMode(View.OVER_SCROLL_NEVER);
        webView.setVerticalScrollBarEnabled(false);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setSupportMultipleWindows(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);

        webView.addJavascriptInterface(new ExternalLinkBridge(), "EnglishQuestAndroid");
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public android.webkit.WebResourceResponse shouldInterceptRequest(
                    WebView view, WebResourceRequest request) {
                return assetLoader.shouldInterceptRequest(request.getUrl());
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return handleNavigation(request.getUrl());
            }

            @SuppressWarnings("deprecation")
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return handleNavigation(Uri.parse(url));
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                installNativeNavigationAndLinkHandlers(view);
                if (ttsReady) notifyWebTtsReady();
                rendererRestartCount = 0;
            }

            @Override
            public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                // The system kills the WebView renderer under memory pressure. Without this
                // override the whole app process is taken down with it, which looks like a crash
                // and loses the screen the learner was on. Rebuild the UI instead, with a restart
                // budget so a persistently failing renderer ends in a clean finish() rather than
                // an endless reload loop. Progress itself is safe: it lives in the profile stored
                // through localStorage / SharedPreferences, not in the renderer.
                if (view != null && view == webView) {
                    try { view.destroy(); } catch (RuntimeException ignored) { }
                    webView = null;
                }
                rendererRestartCount++;
                runOnUiThread(() -> {
                    // MainActivity.this is required here: inside the anonymous WebViewClient,
                    // `this` would be the client, not the Activity that Toast needs.
                    if (rendererRestartCount <= MAX_RENDERER_RESTARTS) {
                        Toast.makeText(MainActivity.this, "Tampilan belajar dimuat ulang setelah gangguan sistem.", Toast.LENGTH_LONG).show();
                        recreate();
                    } else {
                        Toast.makeText(MainActivity.this, "Aplikasi perlu dibuka ulang. Progres belajar tetap tersimpan.", Toast.LENGTH_LONG).show();
                        finish();
                    }
                });
                return true;
            }
        });

        setContentView(webView);
        initializeTextToSpeech();
        webView.loadUrl(APP_ORIGIN + "/assets/index.html");
    }

    private void initializeTextToSpeech() {
        try {
            textToSpeech = new TextToSpeech(this, status -> {
                if (status == TextToSpeech.SUCCESS && textToSpeech != null) {
                    int languageResult = textToSpeech.setLanguage(Locale.US);
                    ttsReady = languageResult >= TextToSpeech.LANG_AVAILABLE;
                    if (ttsReady && Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                        defaultEnglishVoice = findInstalledEnglishVoice(textToSpeech.getVoice());
                        if (defaultEnglishVoice != null) {
                            try { textToSpeech.setVoice(defaultEnglishVoice); } catch (RuntimeException ignored) { }
                        }
                        cacheVoiceGenderCapabilities();
                    }
                    textToSpeech.setSpeechRate(1.0f);
                    textToSpeech.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                        @Override
                        public void onStart(String utteranceId) {
                        }

                        @Override
                        public void onDone(String utteranceId) {
                            notifyWebTtsEnd(utteranceId);
                        }

                        @Override
                        public void onError(String utteranceId) {
                            notifyWebTtsError(utteranceId, "Text-to-Speech engine error");
                        }

                        @Override
                        public void onError(String utteranceId, int errorCode) {
                            notifyWebTtsError(utteranceId, "Text-to-Speech error " + errorCode);
                        }
                    });
                    if (ttsReady) notifyWebTtsReady();
                } else {
                    ttsReady = false;
                }
            });
        } catch (RuntimeException ignored) {
            textToSpeech = null;
            ttsReady = false;
        }
    }

    private void notifyWebTtsReady() {
        WebView view = webView;
        if (view != null) {
            view.post(() -> view.evaluateJavascript(
                    "window.__eqNativeTtsReady&&window.__eqNativeTtsReady()", null));
        }
    }

    private void notifyWebTtsEnd(String utteranceId) {
        evaluateTtsCallback("__eqNativeTtsEnd", utteranceId, null);
    }

    private void notifyWebTtsError(String utteranceId, String message) {
        evaluateTtsCallback("__eqNativeTtsError", utteranceId, message);
    }

    private void evaluateTtsCallback(String callback, String utteranceId, String message) {
        WebView view = webView;
        if (view == null) return;
        String id = JSONObject.quote(utteranceId == null ? "" : utteranceId);
        String js;
        if (message == null) {
            js = "window." + callback + "&&window." + callback + "(" + id + ")";
        } else {
            js = "window." + callback + "&&window." + callback + "(" + id + "," + JSONObject.quote(message) + ")";
        }
        view.post(() -> view.evaluateJavascript(js, null));
    }

    private void speakNative(String text, float rate, String gender, String utteranceId) {
        if (!ttsReady || textToSpeech == null || text == null || text.trim().isEmpty()) {
            notifyWebTtsError(utteranceId, "English TTS is not ready");
            return;
        }
        String safeText = text.length() > 2500 ? text.substring(0, 2500) : text;
        float safeRate = Math.max(0.60f, Math.min(1.20f, rate));
        String safeId = utteranceId == null || utteranceId.isEmpty()
                ? String.valueOf(System.nanoTime()) : utteranceId;
        runOnUiThread(() -> {
            if (!ttsReady || textToSpeech == null) {
                notifyWebTtsError(safeId, "English TTS is not ready");
                return;
            }
            chooseVoice(gender);
            textToSpeech.setSpeechRate(safeRate);
            int result = textToSpeech.speak(safeText, TextToSpeech.QUEUE_FLUSH, null, safeId);
            if (result == TextToSpeech.ERROR) {
                notifyWebTtsError(safeId, "The device could not start the voice");
            }
        });
    }

    private Voice findInstalledEnglishVoice(Voice preferred) {
        if (isInstalledEnglishVoice(preferred)) return preferred;
        if (textToSpeech == null || Build.VERSION.SDK_INT < Build.VERSION_CODES.LOLLIPOP) return preferred;
        try {
            Set<Voice> voices = textToSpeech.getVoices();
            if (voices != null) {
                Voice anyEnglish = null;
                for (Voice voice : voices) {
                    if (!isInstalledEnglishVoice(voice)) continue;
                    if ("US".equalsIgnoreCase(voice.getLocale().getCountry())) return voice;
                    if (anyEnglish == null) anyEnglish = voice;
                }
                if (anyEnglish != null) return anyEnglish;
            }
        } catch (RuntimeException ignored) {
        }
        return preferred;
    }

    private boolean isInstalledEnglishVoice(Voice voice) {
        if (voice == null || voice.getLocale() == null
                || !"en".equalsIgnoreCase(voice.getLocale().getLanguage())) return false;
        try {
            if (voice.isNetworkConnectionRequired()) return false;
            Set<String> features = voice.getFeatures();
            return features == null || !features.contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED);
        } catch (RuntimeException ignored) {
            return false;
        }
    }

    private void cacheVoiceGenderCapabilities() {
        boolean maleAvailable = false;
        boolean femaleAvailable = false;
        if (textToSpeech == null || Build.VERSION.SDK_INT < Build.VERSION_CODES.LOLLIPOP) return;
        try {
            Set<Voice> voices = textToSpeech.getVoices();
            if (voices != null) {
                for (Voice voice : voices) {
                    if (!isInstalledEnglishVoice(voice)) continue;
                    maleAvailable |= matchesVoiceGender(voice.getName(), "male");
                    femaleAvailable |= matchesVoiceGender(voice.getName(), "female");
                }
            }
        } catch (RuntimeException ignored) {
        }
        englishMaleVoiceAvailable = maleAvailable;
        englishFemaleVoiceAvailable = femaleAvailable;
    }

    private boolean supportsVoiceGender(String gender) {
        if (gender == null) return false;
        if ("male".equalsIgnoreCase(gender)) return englishMaleVoiceAvailable;
        if ("female".equalsIgnoreCase(gender)) return englishFemaleVoiceAvailable;
        return false;
    }

    private boolean matchesVoiceGender(String rawName, String preference) {
        String name = rawName == null ? "" : rawName.toLowerCase(Locale.ROOT);
        boolean female = name.contains("female") || name.contains("woman")
                || name.contains("samantha") || name.contains("ava") || name.contains("aria")
                || name.contains("jenny") || name.contains("karen") || name.contains("zira");
        boolean male = !female && (name.contains("male") || name.contains("man")
                || name.contains("daniel") || name.contains("david") || name.contains("alex")
                || name.contains("guy") || name.contains("james") || name.contains("george"));
        return "female".equals(preference) ? female : male;
    }

    private void chooseVoice(String gender) {
        if (textToSpeech == null || Build.VERSION.SDK_INT < Build.VERSION_CODES.LOLLIPOP) return;
        try {
            if (defaultEnglishVoice != null) textToSpeech.setVoice(defaultEnglishVoice);
            if (gender == null || "auto".equalsIgnoreCase(gender)) return;
            String preference = gender.toLowerCase(Locale.ROOT);
            Set<Voice> voices = textToSpeech.getVoices();
            if (voices == null) return;
            for (Voice voice : voices) {
                if (!isInstalledEnglishVoice(voice)) continue;
                if (matchesVoiceGender(voice.getName(), preference)) {
                    textToSpeech.setVoice(voice);
                    return;
                }
            }
            // No matching voice: restore the device's default English voice.
            if (defaultEnglishVoice != null) textToSpeech.setVoice(defaultEnglishVoice);
        } catch (RuntimeException ignored) {
        }
    }

    private boolean isSpeechRecognitionAvailable() {
        // The English acoustic model is bundled in the APK and is independent of
        // Android's SpeechRecognizer service, language packs, and Android 12+.
        return true;
    }

    private boolean isOnlineSpeechRecognitionAvailable() {
        try {
            return SpeechRecognizer.isRecognitionAvailable(this);
        } catch (RuntimeException ignored) {
            return false;
        }
    }

    private boolean openSpeechLanguageSettings() {
        runOnUiThread(() -> {
            try {
                startActivity(new Intent(Settings.ACTION_VOICE_INPUT_SETTINGS));
            } catch (RuntimeException voiceSettingsUnavailable) {
                try {
                    startActivity(new Intent(Settings.ACTION_SETTINGS));
                } catch (RuntimeException ignored) {
                    // Leave the app open if the device has no settings activity.
                }
            }
        });
        return true;
    }

    private boolean requestSpeechRecognition(String target, String requestId,
                                             boolean allowOnlineFallback, boolean forceOnline) {
        if (target == null || target.trim().isEmpty()) return false;
        boolean offlineAvailable = isSpeechRecognitionAvailable();
        boolean onlineServiceAvailable = isOnlineSpeechRecognitionAvailable();
        if (forceOnline && (!allowOnlineFallback || !onlineServiceAvailable)) return false;
        if (!forceOnline && !offlineAvailable && !(allowOnlineFallback && onlineServiceAvailable)) return false;
        String text = target.length() > 220 ? target.substring(0, 220) : target;
        String safeId = requestId == null || requestId.isEmpty()
                ? String.valueOf(System.nanoTime()) : requestId;
        runOnUiThread(() -> beginSpeechRequest(text, safeId, allowOnlineFallback, forceOnline));
        return true;
    }

    private void beginSpeechRequest(String target, String requestId,
                                    boolean allowOnlineFallback, boolean forceOnline) {
        if (speechRequestPending) {
            notifyWebSpeechError(requestId, "Pengenal suara sedang digunakan. Tunggu sebentar lalu coba lagi.");
            return;
        }
        pendingSpeechTarget = target;
        pendingSpeechId = requestId;
        pendingOnlineFallbackAllowed = allowOnlineFallback;
        pendingOnlineAttempted = false;
        pendingForceOnline = forceOnline;
        pendingUsingOnlineRecognizer = false;
        pendingOfflineFailureMessage = "";
        speechRequestPending = true;
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this,
                    new String[]{Manifest.permission.RECORD_AUDIO}, REQUEST_MIC_PERMISSION);
            return;
        }
        launchSpeechRecognizer(forceOnline);
    }

    private void launchSpeechRecognizer(boolean useOnlineRecognizer) {
        if (!speechRequestPending) return;
        if (!useOnlineRecognizer) {
            startOfflineSpeechRecognition();
            return;
        }
        if (useOnlineRecognizer && !pendingOnlineFallbackAllowed) {
            completeSpeechError("Mode online belum diizinkan. Aktifkan persetujuan online di Pengaturan untuk mencobanya.");
            return;
        }
        if (useOnlineRecognizer && !isOnlineSpeechRecognitionAvailable()) {
            completeSpeechError("Layanan pengenal suara online tidak tersedia. Periksa aplikasi/layanan suara Android atau pasang paket English offline.");
            return;
        }
        if (speechRecognizer != null) destroySpeechRecognizer();
        pendingUsingOnlineRecognizer = useOnlineRecognizer;
        final int attempt = ++speechAttemptSerial;
        try {
            // The platform recognizer is only reached for an explicitly allowed online attempt.
            speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this);
            if (useOnlineRecognizer) {
                notifyWebSpeechStatus(pendingSpeechId,
                        pendingOnlineAttempted
                                ? "Mode online yang kamu izinkan aktif setelah mode offline gagal. Ucapkan kembali; layanan Android mungkin memproses audio melalui internet."
                                : "Mode online yang kamu izinkan aktif. Ucapkan sekarang; layanan Android mungkin memproses audio melalui internet.");
            }
            speechRecognizer.setRecognitionListener(new RecognitionListener() {
                @Override public void onReadyForSpeech(Bundle params) { }
                @Override public void onBeginningOfSpeech() { }
                @Override public void onRmsChanged(float rmsdB) { }
                @Override public void onBufferReceived(byte[] buffer) { }
                @Override public void onEndOfSpeech() { }
                @Override public void onEvent(int eventType, Bundle params) { }
                @Override public void onPartialResults(Bundle partialResults) { }

                private boolean isCurrentAttempt() {
                    return speechRequestPending && attempt == speechAttemptSerial
                            && pendingUsingOnlineRecognizer == useOnlineRecognizer;
                }

                @Override
                public void onResults(Bundle results) {
                    if (!isCurrentAttempt()) return;
                    String requestId = pendingSpeechId;
                    ArrayList<String> matches = results == null ? null
                            : results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                    if (matches != null && !matches.isEmpty() && matches.get(0) != null) {
                        Float confidence = null;
                        float[] scores = results.getFloatArray(SpeechRecognizer.CONFIDENCE_SCORES);
                        if (scores != null && scores.length > 0 && scores[0] >= 0f && scores[0] <= 1f) {
                            confidence = scores[0];
                        }
                        clearPendingSpeech();
                        notifyWebSpeechResult(requestId, matches.get(0), confidence);
                    } else {
                        completeSpeechError(useOnlineRecognizer
                                ? combineSpeechAttemptErrors("Ucapan online belum tertangkap. Coba bicara lebih dekat ke mikrofon dan pastikan koneksi internet aktif.")
                                : "Ucapan belum tertangkap. Coba bicara lebih dekat ke mikrofon.");
                    }
                }

                @Override
                public void onError(int error) {
                    if (!isCurrentAttempt()) return;
                    if (!useOnlineRecognizer && shouldUseOnlineFallback(error)
                            && tryStartOnlineFallback(offlineSpeechErrorMessage(error))) return;
                    completeSpeechError(useOnlineRecognizer
                            ? combineSpeechAttemptErrors(onlineSpeechErrorMessage(error))
                            : offlineSpeechErrorMessage(error));
                }
            });

            Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.US.toLanguageTag());
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, Locale.US.toLanguageTag());
            intent.putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, !useOnlineRecognizer);
            intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
            intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false);
            speechRecognizer.startListening(intent);
        } catch (RuntimeException error) {
            if (!useOnlineRecognizer && tryStartOnlineFallback("Pengenal on-device gagal dibuka.")) return;
            completeSpeechError(useOnlineRecognizer
                    ? combineSpeechAttemptErrors("Layanan pengenal online gagal dibuka. Periksa koneksi internet dan layanan suara Android.")
                    : "Pengenalan suara offline gagal dibuka. Periksa paket English dan izin mikrofon.");
        }
    }

    private void startOfflineSpeechRecognition() {
        if (!speechRequestPending) return;
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this,
                    new String[]{Manifest.permission.RECORD_AUDIO}, REQUEST_MIC_PERMISSION);
            return;
        }
        if (voskModel != null) {
            startVoskCapture();
            return;
        }
        if (voskModelLoading) {
            notifyWebSpeechStatus(pendingSpeechId,
                    "Mode offline lokal: menyiapkan model English pertama kali. Audio tetap di perangkat.");
            return;
        }
        voskModelLoading = true;
        notifyWebSpeechStatus(pendingSpeechId,
                "Mode offline lokal: mengekstrak model English (sekali saja). Tunggu sebentar; tidak perlu internet.");
        voskExecutor.execute(() -> {
            org.vosk.Model loaded = null;
            Throwable failure = null;
            try {
                File modelDirectory = ensureBundledVoskModel();
                loaded = new org.vosk.Model(modelDirectory.getAbsolutePath());
            } catch (Exception | LinkageError | OutOfMemoryError error) {
                failure = error;
            }
            final org.vosk.Model result = loaded;
            final Throwable error = failure;
            mainHandler.post(() -> {
                voskModelLoading = false;
                if (isFinishing() || isDestroyed()) {
                    if (result != null) try { result.close(); } catch (RuntimeException ignored) { }
                    return;
                }
                if (result != null) voskModel = result;
                if (!speechRequestPending) return;
                if (result == null) {
                    String message = offlineModelErrorMessage(error);
                    if (tryStartOnlineFallback(message)) return;
                    completeSpeechError(message + " Online tidak digunakan tanpa persetujuan.");
                    return;
                }
                notifyWebSpeechStatus(pendingSpeechId,
                        "Model offline English siap. Mode Vosk lokal aktif; ucapkan kalimat sekarang.");
                startVoskCapture();
            });
        });
    }

    private String offlineModelErrorMessage(Throwable error) {
        if (error instanceof IOException && error.getMessage() != null
                && error.getMessage().contains("Ruang penyimpanan")) return error.getMessage();
        if (error instanceof OutOfMemoryError) {
            return "RAM perangkat belum cukup untuk membuka model English offline. Tutup aplikasi lain, lalu coba lagi; mode online tidak digunakan tanpa persetujuan.";
        }
        if (error instanceof LinkageError) {
            return "Library Vosk tidak cocok dengan arsitektur/perangkat ini. Pastikan memasang APK English Quest terbaru; detail: "
                    + safeThrowableMessage(error);
        }
        String detail = safeThrowableMessage(error);
        if (!detail.isEmpty()) {
            return "Model Vosk offline gagal disiapkan: " + detail
                    + ". Periksa ruang kosong (sekitar 85 MB) lalu coba lagi.";
        }
        return "Model offline English gagal disiapkan. Periksa ruang kosong (sekitar 85 MB), lalu coba lagi.";
    }

    private String safeThrowableMessage(Throwable error) {
        if (error == null || error.getMessage() == null) return "";
        String message = error.getMessage().replace('\n', ' ').replace('\r', ' ').trim();
        return message.length() > 220 ? message.substring(0, 220) : message;
    }

    private File ensureBundledVoskModel() throws IOException {
        File modelDirectory = new File(getFilesDir(), "vosk-model-small-en-us-0.15");
        if (isValidVoskModel(modelDirectory)) return modelDirectory;

        StatFs storage = new StatFs(getFilesDir().getAbsolutePath());
        if (storage.getAvailableBytes() < MIN_MODEL_FREE_BYTES) {
            throw new IOException("Ruang penyimpanan kurang; sisakan sekitar 85 MB untuk menyiapkan model offline.");
        }

        File parent = modelDirectory.getParentFile();
        if (parent == null || (!parent.exists() && !parent.mkdirs())) {
            throw new IOException("Tidak dapat membuat folder privat model offline.");
        }
        File temporaryDirectory = new File(parent, modelDirectory.getName() + ".tmp");
        deleteRecursively(temporaryDirectory);
        if (!temporaryDirectory.mkdirs()) {
            throw new IOException("Tidak dapat menyiapkan folder sementara model offline.");
        }

        long extractedBytes = 0;
        boolean successful = false;
        try (ZipInputStream zip = new ZipInputStream(
                new BufferedInputStream(getAssets().open(VOSK_MODEL_ARCHIVE), 64 * 1024))) {
            byte[] buffer = new byte[64 * 1024];
            ZipEntry entry;
            String temporaryRoot = temporaryDirectory.getCanonicalPath() + File.separator;
            while ((entry = zip.getNextEntry()) != null) {
                String entryName = entry.getName();
                if (entryName == null || !entryName.startsWith(VOSK_MODEL_ROOT)) {
                    throw new IOException("Arsip model offline memiliki struktur yang tidak valid.");
                }
                String relative = entryName.substring(VOSK_MODEL_ROOT.length());
                if (relative.isEmpty()) {
                    zip.closeEntry();
                    continue;
                }
                File output = new File(temporaryDirectory, relative);
                String outputPath = output.getCanonicalPath();
                if (!outputPath.startsWith(temporaryRoot)) {
                    throw new IOException("Arsip model offline memiliki jalur file yang tidak aman.");
                }
                if (entry.isDirectory()) {
                    if (!output.isDirectory() && !output.mkdirs()) {
                        throw new IOException("Tidak dapat membuat folder model offline.");
                    }
                } else {
                    File outputParent = output.getParentFile();
                    if (outputParent == null || (!outputParent.exists() && !outputParent.mkdirs())) {
                        throw new IOException("Tidak dapat membuat folder file model offline.");
                    }
                    try (BufferedOutputStream stream = new BufferedOutputStream(
                            new FileOutputStream(output), 64 * 1024)) {
                        int count;
                        while ((count = zip.read(buffer)) != -1) {
                            stream.write(buffer, 0, count);
                            extractedBytes += count;
                        }
                    }
                }
                zip.closeEntry();
            }
            if (extractedBytes < MIN_EXTRACTED_MODEL_BYTES) {
                throw new IOException("Arsip model offline tidak lengkap.");
            }
            if (!isValidVoskModel(temporaryDirectory)) {
                throw new IOException("Model offline tidak lolos pemeriksaan berkas.");
            }
            deleteRecursively(modelDirectory);
            if (!temporaryDirectory.renameTo(modelDirectory)) {
                throw new IOException("Tidak dapat menyimpan model offline di penyimpanan privat.");
            }
            successful = true;
        } finally {
            if (!successful) deleteRecursively(temporaryDirectory);
        }
        return modelDirectory;
    }

    private boolean isValidVoskModel(File directory) {
        File acousticModel = new File(directory, "am/final.mdl");
        File featureConfig = new File(directory, "conf/mfcc.conf");
        File grammar = new File(directory, "graph/Gr.fst");
        File topology = new File(directory, "graph/HCLr.fst");
        return directory.isDirectory()
                && acousticModel.isFile() && acousticModel.length() > 1_000_000L
                && featureConfig.isFile() && featureConfig.length() > 0L
                && grammar.isFile() && grammar.length() > 1_000_000L
                && topology.isFile() && topology.length() > 1_000_000L;
    }

    private void deleteRecursively(File file) {
        if (file == null || !file.exists()) return;
        File[] children = file.listFiles();
        if (children != null) {
            for (File child : children) deleteRecursively(child);
        }
        file.delete();
    }

    private AudioRecord createOfflineAudioRecorder() throws IOException {
        int minimumBufferBytes = AudioRecord.getMinBufferSize(
                VOSK_SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (minimumBufferBytes <= 0) {
            throw new IOException("Perangkat tidak menyediakan input mikrofon PCM mono 16 kHz (kode "
                    + minimumBufferBytes + ").");
        }
        int bufferBytes = Math.max(minimumBufferBytes, VOSK_AUDIO_BUFFER_SAMPLES * 2);
        int[] sources = {MediaRecorder.AudioSource.VOICE_RECOGNITION, MediaRecorder.AudioSource.MIC};
        String[] sourceNames = {"VOICE_RECOGNITION", "MIC"};
        IOException lastFailure = null;
        for (int index = 0; index < sources.length; index++) {
            AudioRecord candidate = null;
            try {
                candidate = new AudioRecord(sources[index], VOSK_SAMPLE_RATE,
                        AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufferBytes);
                if (candidate.getState() != AudioRecord.STATE_INITIALIZED) {
                    lastFailure = new IOException("AudioRecord " + sourceNames[index]
                            + " tidak dapat diinisialisasi pada 16 kHz.");
                } else {
                    candidate.startRecording();
                    if (candidate.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING) {
                        return candidate;
                    }
                    lastFailure = new IOException("AudioRecord " + sourceNames[index]
                            + " gagal masuk ke status RECORDING.");
                }
            } catch (IllegalArgumentException | IllegalStateException | SecurityException error) {
                lastFailure = new IOException("AudioRecord " + sourceNames[index] + " gagal: "
                        + safeThrowableMessage(error), error);
            }
            if (candidate != null) {
                try { candidate.release(); } catch (RuntimeException ignored) { }
            }
        }
        if (lastFailure != null) throw lastFailure;
        throw new IOException("Tidak ada sumber mikrofon lokal yang dapat dibuka.");
    }

    private boolean isCurrentOfflineSpeechRequest(String requestId, int attempt) {
        return speechRequestPending && attempt == speechAttemptSerial
                && requestId.equals(pendingSpeechId);
    }

    private void startVoskCapture() {
        if (!speechRequestPending || voskModel == null) return;
        Thread previousThread = offlineAudioThread;
        if (previousThread != null && previousThread.isAlive()) {
            stopOfflineCapture(false);
            if (previousThread.isAlive()) {
                notifyWebSpeechStatus(pendingSpeechId,
                        "Mode offline lokal: menutup sesi mikrofon sebelumnya. Tunggu sebentar lalu ulangi.");
                mainHandler.postDelayed(() -> startVoskCapture(), 180L);
                return;
            }
        }
        final String requestId = pendingSpeechId;
        final int attempt = ++speechAttemptSerial;
        org.vosk.Recognizer createdRecognizer = null;
        AudioRecord createdRecorder = null;
        try {
            createdRecognizer = new org.vosk.Recognizer(voskModel, (float) VOSK_SAMPLE_RATE);
            createdRecorder = createOfflineAudioRecorder();
            final org.vosk.Recognizer activeRecognizer = createdRecognizer;
            final AudioRecord activeRecorder = createdRecorder;
            offlineRecognizer = activeRecognizer;
            offlineRecorder = activeRecorder;
            offlineCollectFinalResult = false;
            offlineCaptureRunning = true;
            Thread captureThread = new Thread(
                    () -> runOfflineCapture(requestId, attempt, activeRecognizer, activeRecorder),
                    "EnglishQuest-Vosk-Audio-" + attempt);
            offlineAudioThread = captureThread;
            captureThread.start();
            notifyWebSpeechStatus(requestId,
                    "Mode offline lokal Vosk aktif. Ucapkan kalimat English sekarang; audio tidak dikirim ke internet.");
            offlineSpeechTimeout = () -> {
                if (!isCurrentOfflineSpeechRequest(requestId, attempt)) return;
                stopOfflineCapture(true);
            };
            mainHandler.postDelayed(offlineSpeechTimeout, 12000L);
        } catch (Exception | LinkageError | OutOfMemoryError error) {
            offlineCaptureRunning = false;
            offlineCollectFinalResult = false;
            if (createdRecorder != null) {
                try {
                    if (createdRecorder.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING) {
                        createdRecorder.stop();
                    }
                } catch (RuntimeException ignored) { }
                try { createdRecorder.release(); } catch (RuntimeException ignored) { }
            }
            if (createdRecognizer != null) {
                try { createdRecognizer.close(); } catch (RuntimeException ignored) { }
            }
            offlineRecorder = null;
            offlineRecognizer = null;
            offlineAudioThread = null;
            String detail = "Perekam offline lokal gagal dimulai: " + safeThrowableMessage(error);
            offlineRecognitionFailed(detail);
        }
    }

    private void runOfflineCapture(String requestId, int attempt,
                                   org.vosk.Recognizer recognizer, AudioRecord recorder) {
        String transcript = "";
        String failure = "";
        boolean collectFinalResult = false;
        try {
            Process.setThreadPriority(Process.THREAD_PRIORITY_AUDIO);
            short[] buffer = new short[VOSK_AUDIO_BUFFER_SAMPLES];
            while (offlineCaptureRunning && isCurrentOfflineSpeechRequest(requestId, attempt)) {
                int samplesRead = recorder.read(buffer, 0, buffer.length, AudioRecord.READ_BLOCKING);
                if (samplesRead < 0) {
                    if (!offlineCaptureRunning) break;
                    throw new IOException("AudioRecord.read() failed with code " + samplesRead + ".");
                }
                if (samplesRead == 0) continue;
                if (recognizer.acceptWaveForm(buffer, samplesRead)) {
                    transcript = extractVoskTranscript(recognizer.getResult());
                    if (!transcript.isEmpty()) break;
                }
            }
            collectFinalResult = offlineCollectFinalResult
                    && isCurrentOfflineSpeechRequest(requestId, attempt);
            if (transcript.isEmpty() && collectFinalResult) {
                transcript = extractVoskTranscript(recognizer.getFinalResult());
            }
        } catch (Exception | LinkageError | OutOfMemoryError error) {
            if (offlineCaptureRunning && isCurrentOfflineSpeechRequest(requestId, attempt)) {
                failure = "Perekaman/pengenalan Vosk lokal gagal: " + safeThrowableMessage(error);
            }
        } finally {
            offlineCaptureRunning = false;
            try {
                if (recorder.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING) recorder.stop();
            } catch (RuntimeException ignored) { }
            try { recorder.release(); } catch (RuntimeException ignored) { }
            try { recognizer.close(); } catch (RuntimeException ignored) { }
        }
        final String finalTranscript = transcript;
        final String finalFailure = failure;
        final boolean didRequestFinalResult = collectFinalResult;
        final Thread captureThread = Thread.currentThread();
        mainHandler.post(() -> {
            if (offlineRecorder == recorder) offlineRecorder = null;
            if (offlineRecognizer == recognizer) offlineRecognizer = null;
            if (offlineAudioThread == captureThread) offlineAudioThread = null;
            offlineCollectFinalResult = false;
            if (!isCurrentOfflineSpeechRequest(requestId, attempt)) return;
            if (!finalFailure.isEmpty()) {
                offlineRecognitionFailed(finalFailure);
            } else if (!finalTranscript.isEmpty()) {
                finishVoskTranscript(requestId, attempt, finalTranscript);
            } else if (didRequestFinalResult) {
                offlineRecognitionFailed("Tidak ada ucapan yang tertangkap. Coba bicara lebih dekat ke mikrofon, periksa izin mikrofon, lalu ulangi.");
            }
        });
    }

    private String extractVoskTranscript(String hypothesis) {
        try {
            return new JSONObject(hypothesis == null ? "{}" : hypothesis)
                    .optString("text", "").trim();
        } catch (Exception ignored) {
            return "";
        }
    }

    private void finishVoskTranscript(String requestId, int attempt, String transcript) {
        if (!speechRequestPending || attempt != speechAttemptSerial
                || !requestId.equals(pendingSpeechId)) return;
        clearPendingSpeech();
        notifyWebSpeechResult(requestId, transcript, null);
    }

    private void offlineRecognitionFailed(String message) {
        if (!speechRequestPending) return;
        stopOfflineCapture(false);
        if (tryStartOnlineFallback(message)) return;
        completeSpeechError(message + " Mode online tidak digunakan tanpa persetujuan.");
    }

    private void stopOfflineCapture(boolean collectFinalResult) {
        if (offlineSpeechTimeout != null) {
            mainHandler.removeCallbacks(offlineSpeechTimeout);
            offlineSpeechTimeout = null;
        }
        Thread captureThread = offlineAudioThread;
        AudioRecord recorder = offlineRecorder;
        offlineCollectFinalResult = collectFinalResult;
        offlineCaptureRunning = false;
        if (recorder != null) {
            try {
                if (recorder.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING) recorder.stop();
            } catch (RuntimeException ignored) { }
        }
        if (captureThread != null && captureThread != Thread.currentThread()) {
            try { captureThread.join(600L); }
            catch (InterruptedException error) { Thread.currentThread().interrupt(); }
        }
    }

    private boolean shouldUseOnlineFallback(int error) {
        if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS
                || error == SpeechRecognizer.ERROR_NO_MATCH
                || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT
                || error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY
                || error == SpeechRecognizer.ERROR_CLIENT
                || error == SpeechRecognizer.ERROR_AUDIO) return false;
        return error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED
                || error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE
                || error == SpeechRecognizer.ERROR_NETWORK
                || error == SpeechRecognizer.ERROR_NETWORK_TIMEOUT
                || error == SpeechRecognizer.ERROR_SERVER;
    }

    private boolean tryStartOnlineFallback(String offlineFailureMessage) {
        if (!speechRequestPending || !pendingOnlineFallbackAllowed || pendingOnlineAttempted
                || !isOnlineSpeechRecognitionAvailable()) return false;
        pendingOfflineFailureMessage = offlineFailureMessage == null ? "" : offlineFailureMessage.trim();
        pendingOnlineAttempted = true;
        pendingUsingOnlineRecognizer = true;
        speechAttemptSerial++;
        destroySpeechRecognizer();
        String statusMessage = pendingOfflineFailureMessage
                + " Fallback online yang kamu izinkan sedang dicoba; layanan Android mungkin memproses audio melalui internet. Ucapkan kembali.";
        notifyWebSpeechStatus(pendingSpeechId, statusMessage);
        WebView view = webView;
        if (view != null) {
            view.postDelayed(() -> launchSpeechRecognizer(true), 150L);
        } else {
            runOnUiThread(() -> launchSpeechRecognizer(true));
        }
        return true;
    }

    private String offlineSpeechErrorMessage(int error) {
        switch (error) {
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS:
                return "Izin mikrofon ditolak. Izinkan mikrofon lalu coba lagi.";
            case SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED:
            case SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE:
                return "Paket pengenalan English offline belum tersedia. Pasang paket bahasa offline di pengaturan Android; online hanya digunakan jika kamu mengizinkannya.";
            case SpeechRecognizer.ERROR_NO_MATCH:
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT:
                return "Ucapan belum tertangkap. Coba lagi dengan suara yang lebih jelas.";
            case SpeechRecognizer.ERROR_NETWORK:
            case SpeechRecognizer.ERROR_NETWORK_TIMEOUT:
                return "Layanan pengenalan offline gagal. Periksa paket bahasa lokal; online hanya dicoba bila kamu mengizinkannya.";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY:
                return "Pengenal offline sedang sibuk. Tunggu sebentar lalu coba lagi.";
            default:
                return "Pengenalan ucapan offline gagal. Mode online tidak digunakan tanpa persetujuan; coba lagi atau gunakan panduan baca.";
        }
    }

    private String onlineSpeechErrorMessage(int error) {
        switch (error) {
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS:
                return "Izin mikrofon ditolak. Izinkan mikrofon lalu coba lagi.";
            case SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED:
            case SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE:
                return "Layanan online belum mendukung English saat ini. Periksa bahasa pada layanan suara Android.";
            case SpeechRecognizer.ERROR_NO_MATCH:
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT:
                return "Ucapan online belum tertangkap. Coba bicara lebih dekat ke mikrofon.";
            case SpeechRecognizer.ERROR_NETWORK:
            case SpeechRecognizer.ERROR_NETWORK_TIMEOUT:
                return "Pengenalan online gagal karena koneksi. Periksa internet lalu coba lagi.";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY:
                return "Pengenal suara sedang sibuk. Tunggu sebentar lalu coba lagi.";
            default:
                return "Pengenalan suara online gagal. Periksa koneksi atau layanan Android; audio/transkrip tidak disimpan oleh aplikasi.";
        }
    }

    private String combineSpeechAttemptErrors(String onlineMessage) {
        if (!pendingOnlineAttempted) return onlineMessage;
        String offlineMessage = pendingOfflineFailureMessage == null || pendingOfflineFailureMessage.trim().isEmpty()
                ? "Pengenal on-device atau paket bahasa English offline belum siap."
                : pendingOfflineFailureMessage.trim();
        return "Pengenal lokal Vosk gagal: " + offlineMessage
                + " Percobaan online yang kamu izinkan juga gagal: " + onlineMessage
                + " Periksa izin mikrofon atau ruang penyimpanan, lalu coba offline lagi. Rekaman tidak dikirim saat mode Vosk lokal digunakan.";
    }

    private void destroySpeechRecognizer() {
        SpeechRecognizer current = speechRecognizer;
        speechRecognizer = null;
        if (current != null) {
            try { current.cancel(); } catch (RuntimeException ignored) { }
            try { current.destroy(); } catch (RuntimeException ignored) { }
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQUEST_MIC_PERMISSION) return;
        if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            launchSpeechRecognizer(pendingForceOnline);
        } else {
            completeSpeechError("Izin mikrofon ditolak. Izinkan mikrofon lalu coba lagi.");
        }
    }

    private void notifyWebSpeechResult(String requestId, String transcript, Float confidence) {
        String id = JSONObject.quote(requestId == null ? "" : requestId);
        String spoken = JSONObject.quote(transcript == null ? "" : transcript);
        String score = confidence == null ? "null" : Float.toString(confidence);
        WebView view = webView;
        if (view != null) {
            String js = "window.__eqNativeSpeechResult&&window.__eqNativeSpeechResult(" + id + "," + spoken + "," + score + ")";
            view.post(() -> view.evaluateJavascript(js, null));
        }
    }

    private void notifyWebSpeechStatus(String requestId, String message) {
        String id = JSONObject.quote(requestId == null ? "" : requestId);
        String status = JSONObject.quote(message == null ? "Mendengarkan…" : message);
        WebView view = webView;
        if (view != null) {
            String js = "window.__eqNativeSpeechStatus&&window.__eqNativeSpeechStatus(" + id + "," + status + ")";
            view.post(() -> view.evaluateJavascript(js, null));
        }
    }

    private void notifyWebSpeechError(String requestId, String message) {
        String id = JSONObject.quote(requestId == null ? "" : requestId);
        String error = JSONObject.quote(message == null ? "Microphone input failed" : message);
        WebView view = webView;
        if (view != null) {
            String js = "window.__eqNativeSpeechError&&window.__eqNativeSpeechError(" + id + "," + error + ")";
            view.post(() -> view.evaluateJavascript(js, null));
        }
    }

    private void completeSpeechError(String message) {
        String requestId = pendingSpeechId;
        clearPendingSpeech();
        notifyWebSpeechError(requestId, message);
    }

    private void clearPendingSpeech() {
        stopOfflineCapture(false);
        speechRequestPending = false;
        pendingSpeechId = "";
        pendingSpeechTarget = "";
        pendingOnlineFallbackAllowed = false;
        pendingOnlineAttempted = false;
        pendingForceOnline = false;
        pendingUsingOnlineRecognizer = false;
        pendingOfflineFailureMessage = "";
        speechAttemptSerial++;
        destroySpeechRecognizer();
    }

    private boolean handleNavigation(Uri uri) {
        if (APP_ORIGIN.equals(uri.getScheme() + "://" + uri.getHost())) {
            return false;
        }
        openExternal(uri.toString());
        return true;
    }

    private void installNativeNavigationAndLinkHandlers(WebView view) {
        String bridgeScript = "(function(){"
                + "if(window.__eqAndroidHandlersInstalled)return;"
                + "window.__eqAndroidHandlersInstalled=true;"
                + "document.addEventListener('click',function(event){"
                + "var node=event.target;"
                + "while(node&&node!==document&&!(node.matches&&node.matches('a[target=\\\"_blank\\\"]')))node=node.parentElement;"
                + "if(node&&node.matches&&node.matches('a[target=\\\"_blank\\\"]')){"
                + "event.preventDefault();"
                + "if(window.EnglishQuestAndroid)window.EnglishQuestAndroid.openExternal(node.href);"
                + "}},true);"
                + "window.__eqNativeBack=function(){"
                + "try{if(typeof state==='undefined'||state.page==='home'||state.page==='welcome')return false;"
                + "if(typeof goBack==='function'){goBack();return true;}}catch(error){}return false;};"
                + "})();";
        view.evaluateJavascript(bridgeScript, null);
    }

    private void openExternal(String rawUrl) {
        try {
            Uri uri = Uri.parse(rawUrl);
            String scheme = uri.getScheme();
            String host = uri.getHost();
            if (!"https".equalsIgnoreCase(scheme) || host == null) return;
            boolean allowedHost = host.equals("en.wikipedia.org")
                    || host.endsWith(".wikipedia.org")
                    || host.equals("creativecommons.org")
                    || host.endsWith(".creativecommons.org");
            boolean whatsappLink = "wa.me".equalsIgnoreCase(host)
                    && uri.getUserInfo() == null && (uri.getPort() == -1 || uri.getPort() == 443)
                    && uri.getPath() != null && uri.getPath().matches("/[1-9][0-9]{7,14}");
            if (!allowedHost && !whatsappLink) return;
            runOnUiThread(() -> {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                } catch (ActivityNotFoundException ignored) {
                    // No browser installed: stay in the learning app.
                }
            });
        } catch (RuntimeException ignored) {
            // Ignore malformed or unsupported external URLs.
        }
    }

    /**
     * The account API origin this APK was built with (BuildConfig.EQ_API_BASE_URL), or an empty
     * string when the build intentionally has no server. It is a public HTTPS origin, never a
     * secret: credentials and tokens live in the server and are fetched with the user's session.
     */
    private String apiBaseUrl() {
        String configured = BuildConfig.EQ_API_BASE_URL;
        return configured == null ? "" : configured.trim();
    }

    /**
     * Local testing escape hatch: a debug build may be compiled with -PEQ_ALLOW_HTTP_DEV=1 so the
     * phone can reach a development server over plain HTTP on a LAN address. Release builds always
     * return false, so a build handed to users can never be downgraded to plain HTTP.
     */
    private boolean httpDevAllowed() {
        return BuildConfig.DEBUG && "1".equals(BuildConfig.EQ_ALLOW_HTTP_DEV);
    }

    private final class ExternalLinkBridge {
        /**
         * Hands the build-time API origin to the web layer.
         *
         * The web client asks the native bridge for this URL and falls back to the page origin
         * when it is missing. The page origin inside the app is the WebView asset origin
         * (https://appassets.androidplatform.net), which the client deliberately refuses to use as
         * an API base — so before this method existed the app could never reach the account API
         * and registration stayed permanently unavailable, no matter what the APK was built with.
         */
        @JavascriptInterface
        public String getApiBaseUrl() {
            return MainActivity.this.apiBaseUrl();
        }

        /** Whether plain-HTTP development addresses are allowed (debug builds only). */
        @JavascriptInterface
        public boolean isHttpDevAllowed() {
            return MainActivity.this.httpDevAllowed();
        }

        @JavascriptInterface
        public void openExternal(String rawUrl) {
            MainActivity.this.openExternal(rawUrl);
        }

        @JavascriptInterface
        public boolean isOnDeviceSpeechRecognitionAvailable() {
            return MainActivity.this.isSpeechRecognitionAvailable();
        }

        @JavascriptInterface
        public boolean isSpeechRecognitionAvailable() {
            return MainActivity.this.isSpeechRecognitionAvailable();
        }

        @JavascriptInterface
        public boolean isOnlineSpeechRecognitionAvailable() {
            return MainActivity.this.isOnlineSpeechRecognitionAvailable();
        }

        @JavascriptInterface
        public boolean openSpeechLanguageSettings() {
            return MainActivity.this.openSpeechLanguageSettings();
        }

        @JavascriptInterface
        public String readSavedProfile() {
            try {
                return MainActivity.this.getSharedPreferences(PROFILE_PREFERENCES, MainActivity.MODE_PRIVATE)
                        .getString(PROFILE_JSON_KEY, "");
            } catch (RuntimeException ignored) {
                return "";
            }
        }

        @JavascriptInterface
        public boolean saveSavedProfile(String rawJson) {
            if (rawJson == null || rawJson.trim().isEmpty() || rawJson.length() > 1_000_000) return false;
            try {
                new JSONObject(rawJson);
                return MainActivity.this.getSharedPreferences(PROFILE_PREFERENCES, MainActivity.MODE_PRIVATE)
                        .edit().putString(PROFILE_JSON_KEY, rawJson).commit();
            } catch (Exception ignored) {
                return false;
            }
        }

        @JavascriptInterface
        public boolean clearSavedProfile() {
            try {
                return MainActivity.this.getSharedPreferences(PROFILE_PREFERENCES, MainActivity.MODE_PRIVATE)
                        .edit().remove(PROFILE_JSON_KEY).commit();
            } catch (RuntimeException ignored) {
                return false;
            }
        }

        @JavascriptInterface
        public boolean startSpeechRecognition(String target, String requestId,
                                              boolean allowOnlineFallback, boolean forceOnline) {
            return MainActivity.this.requestSpeechRecognition(target, requestId, allowOnlineFallback, forceOnline);
        }

        @JavascriptInterface
        public boolean cancelSpeechRecognition(String requestId) {
            if (requestId == null || requestId.trim().isEmpty()) return false;
            runOnUiThread(() -> {
                if (speechRequestPending && requestId.equals(pendingSpeechId)) clearPendingSpeech();
            });
            return true;
        }

        @JavascriptInterface
        public boolean isTtsReady() {
            return ttsReady && textToSpeech != null;
        }

        @JavascriptInterface
        public boolean supportsVoiceGender(String gender) {
            return MainActivity.this.supportsVoiceGender(gender);
        }

        @JavascriptInterface
        public boolean speakNative(String text, double rate, String gender, String utteranceId) {
            if (!isTtsReady()) return false;
            MainActivity.this.speakNative(text, (float) rate, gender, utteranceId);
            return true;
        }

        @JavascriptInterface
        public void stopNativeTts() {
            runOnUiThread(() -> {
                if (textToSpeech != null) textToSpeech.stop();
            });
        }
    }

    @Override
    protected void onPause() {
        WebView view = webView;
        if (view != null) {
            view.evaluateJavascript("window.__eqPersistProfile&&window.__eqPersistProfile()", null);
        }
        super.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        WebView view = webView;
        if (view != null) {
            view.postDelayed(() -> view.evaluateJavascript(
                    "window.__eqNativeSpeechCapabilitiesChanged&&window.__eqNativeSpeechCapabilitiesChanged()", null), 120L);
        }
    }

    @Override
    public void onBackPressed() {
        if (webView == null) {
            super.onBackPressed();
            return;
        }
        webView.evaluateJavascript(
                "window.__eqNativeBack ? window.__eqNativeBack() : false",
                value -> {
                    if (!"true".equals(value)) {
                        finish();
                    }
                });
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.stopLoading();
            webView.removeJavascriptInterface("EnglishQuestAndroid");
            webView.destroy();
            webView = null;
        }
        if (textToSpeech != null) {
            textToSpeech.stop();
            textToSpeech.shutdown();
            textToSpeech = null;
        }
        clearPendingSpeech();
        voskExecutor.shutdownNow();
        org.vosk.Model localModel = voskModel;
        voskModel = null;
        if (localModel != null) {
            try { localModel.close(); } catch (RuntimeException ignored) { }
        }
        defaultEnglishVoice = null;
        englishMaleVoiceAvailable = false;
        englishFemaleVoiceAvailable = false;
        ttsReady = false;
        super.onDestroy();
    }
}
