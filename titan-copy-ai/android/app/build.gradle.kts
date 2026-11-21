plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
    id("com.google.devtools.ksp")
    id("com.chaquo.python")
}

android {
    namespace = "com.titan.copyai"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.titan.copyai"
        minSdk = 28
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // arm64-v8a = real phones; x86_64 = emulator (CI). Phone-only APK: -PtitanAbis=arm64-v8a
        val abis = (project.findProperty("titanAbis") as String? ?: "arm64-v8a,x86_64").split(",").map { it.trim() }
        ndk { abiFilters += abis }
    }

    signingConfigs {
        create("release") {
            val ks = System.getenv("TITAN_KEYSTORE_FILE")
            if (ks != null && file(ks).exists()) {
                storeFile = file(ks)
                storePassword = System.getenv("TITAN_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("TITAN_KEY_ALIAS")
                keyPassword = System.getenv("TITAN_KEY_PASSWORD")
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            val rel = signingConfigs.getByName("release")
            // Owner key when provided; -PtestSigning signs a minified TEST build with the debug key.
            signingConfig = when {
                rel.storeFile != null -> rel
                project.hasProperty("testSigning") -> signingConfigs.getByName("debug")
                else -> null
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}

// The whole TITAN engine (copy engine, risk, agents, SQLite, API on 127.0.0.1) runs ON THE PHONE
// in embedded Python. Packages mirror backend/requirements-android.txt (no pydantic: no Android build).
chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            install("numpy==1.26.2")
            install("cryptography==42.0.8")
            install("SQLAlchemy==2.1.9")
            install("starlette==1.10.0")
            install("uvicorn==0.54.0")
            install("websockets==17.4")
            install("httpx==0.28.1")
            install("python-multipart==0.0.34")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("../../backend")
        }
    }
}

ksp {
    arg("room.schemaLocation", "$projectDir/schemas")
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.12.01")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.navigation:navigation-compose:2.8.5")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-kotlinx-serialization:2.11.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    debugImplementation("androidx.compose.ui:ui-tooling")

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation(composeBom)
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
