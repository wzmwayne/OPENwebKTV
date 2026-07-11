# OwK Player - ProGuard Rules
-keepattributes *Annotation*
-keepattributes JavascriptInterface
-keepclassmembers class * { @android.webkit.JavascriptInterface <methods>; }
