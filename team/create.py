"""CREATE — scaffold a new project, sign it, wire secrets, render CI, and onboard it.

Zero project names — completely generic across Android apps.
Follows the dev-team create-agent contract (skills/new-project/SKILL.md).
"""
from __future__ import annotations

import base64
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import ci
from .core import PROJECTS_BASES, pipeline_dir, save_json, now_iso

DEFAULT_BASE = Path.home() / "AndroidStudioProjects"


def generate_keystore(dest: Path, alias: str = "upload") -> tuple[str, str]:
    """Generate a 2048-bit RSA release keystore and return (store_password, key_password)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    pw = secrets.token_urlsafe(18)
    cmd = [
        "keytool", "-genkeypair", "-v",
        "-keystore", str(dest),
        "-alias", alias,
        "-keyalg", "RSA",
        "-keysize", "2048",
        "-validity", "10000",
        "-storepass", pw,
        "-keypass", pw,
        "-dname", f"CN={dest.stem}, OU=Mobile, O=DabblePoint, C=US"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"keytool failed: {res.stderr}")
    return pw, pw


def scaffold_android_project(
    name: str,
    package: str,
    target_dir: Path,
    title: str = "",
    description: str = "",
) -> Path:
    """Scaffold a standard Android Jetpack Compose project with clean architecture."""
    target_dir.mkdir(parents=True, exist_ok=True)
    pkg_dir = target_dir / "app/src/main/java" / package.replace(".", "/")
    pkg_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy gradle wrapper from MindfulLauncher if available
    ml = Path.home() / "AndroidStudioProjects" / "MindfulLauncher"
    if ml.exists():
        if (ml / "gradlew").exists():
            shutil.copy2(ml / "gradlew", target_dir / "gradlew")
            (target_dir / "gradlew").chmod(0o755)
        if (ml / "gradle").exists():
            shutil.copytree(ml / "gradle", target_dir / "gradle", dirs_exist_ok=True)
    
    # 2. Root build.gradle
    root_gradle = """buildscript {
    ext.kotlin_version = '1.9.24'

    repositories {
        google()
        mavenCentral()
    }

    ext {
        lifecycleVersion  = '2.8.2'
        composeBomVersion = '2024.06.00'
        roomVersion       = '2.6.1'
    }

    dependencies {
        classpath 'com.android.tools.build:gradle:8.13.2'
        classpath "org.jetbrains.kotlin:kotlin-gradle-plugin:$kotlin_version"
    }
}

allprojects {
    repositories {
        google()
        mavenCentral()
    }
}

task clean(type: Delete) {
    delete rootProject.buildDir
}
"""
    (target_dir / "build.gradle").write_text(root_gradle)

    # 3. settings.gradle
    (target_dir / "settings.gradle").write_text(f"""rootProject.name = "{name}"
include ':app'
""")

    # 4. gradle.properties
    (target_dir / "gradle.properties").write_text("""org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8
android.useAndroidX=true
android.enableJetifier=false
kotlin.code.style=official
""")

    # 5. app/build.gradle
    app_gradle = f"""apply plugin: 'com.android.application'
apply plugin: 'kotlin-android'
apply plugin: 'kotlin-kapt'

def keystorePropertiesFile = rootProject.file("keystore.properties")
def keystoreProperties = new Properties()
if (keystorePropertiesFile.exists()) {{
    keystoreProperties.load(new FileInputStream(keystorePropertiesFile))
}}

android {{
    compileSdk 34
    namespace '{package}'

    defaultConfig {{
        applicationId "{package}"
        minSdkVersion 26
        targetSdkVersion 34
        versionCode (System.getenv("VERSION_CODE")?.toInteger() ?: 10000)
        versionName (System.getenv("VERSION_NAME") ?: "1.0.0")
        testInstrumentationRunner "androidx.test.runner.AndroidJUnitRunner"
    }}

    signingConfigs {{
        release {{
            storeFile keystoreProperties['storeFile'] ? rootProject.file(keystoreProperties['storeFile']) : null
            storePassword keystoreProperties['storePassword']
            keyAlias keystoreProperties['keyAlias']
            keyPassword keystoreProperties['keyPassword']
        }}
    }}

    buildTypes {{
        release {{
            signingConfig signingConfigs.release
            minifyEnabled false
            proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'
        }}
        debug {{
            signingConfig signingConfigs.debug
        }}
    }}

    compileOptions {{
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }}

    kotlinOptions {{
        jvmTarget = "17"
    }}

    buildFeatures {{
        compose true
    }}

    composeOptions {{
        kotlinCompilerExtensionVersion '1.5.14'
    }}

    packaging {{
        resources {{
            excludes += [
                'META-INF/LICENSE.md',
                'META-INF/LICENSE-notice.md',
                'META-INF/NOTICE.md',
            ]
        }}
    }}
}}

dependencies {{
    // Jetpack Compose
    implementation platform("androidx.compose:compose-bom:$rootProject.composeBomVersion")
    implementation 'androidx.compose.ui:ui'
    implementation 'androidx.compose.ui:ui-text-google-fonts'
    implementation 'androidx.compose.ui:ui-tooling-preview'
    implementation 'androidx.compose.material3:material3'
    implementation 'androidx.compose.material:material-icons-extended'
    implementation 'androidx.activity:activity-compose:1.9.0'
    debugImplementation 'androidx.compose.ui:ui-tooling'

    // Architecture & Lifecycle
    implementation "androidx.lifecycle:lifecycle-viewmodel-ktx:$rootProject.lifecycleVersion"
    implementation "androidx.lifecycle:lifecycle-runtime-ktx:$rootProject.lifecycleVersion"
    implementation "androidx.lifecycle:lifecycle-viewmodel-compose:$rootProject.lifecycleVersion"
    implementation "androidx.lifecycle:lifecycle-runtime-compose:$rootProject.lifecycleVersion"
    implementation 'androidx.navigation:navigation-compose:2.7.7'

    // Room Database
    implementation "androidx.room:room-runtime:$rootProject.roomVersion"
    implementation "androidx.room:room-ktx:$rootProject.roomVersion"
    kapt "androidx.room:room-compiler:$rootProject.roomVersion"

    // Image loading
    implementation "io.coil-kt:coil-compose:2.6.0"

    // Core Android & Coroutines
    implementation 'androidx.core:core-ktx:1.13.1'
    implementation 'androidx.appcompat:appcompat:1.7.0'
    implementation 'com.google.android.material:material:1.12.0'
    implementation 'org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1'

    // Testing
    testImplementation 'junit:junit:4.13.2'
    testImplementation 'org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1'
}}
"""
    (target_dir / "app/build.gradle").write_text(app_gradle)
    (target_dir / "app/proguard-rules.pro").write_text("# Add project specific ProGuard rules here.\n")

    # 6. AndroidManifest.xml
    manifest = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <uses-permission android:name="android.permission.CAMERA" />
    <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION" />
    <uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION" />

    <application
        android:name=".FieldCertApp"
        android:allowBackup="true"
        android:icon="@mipmap/ic_launcher"
        android:label="@string/app_name"
        android:roundIcon="@mipmap/ic_launcher_round"
        android:supportsRtl="true"
        android:theme="@style/Theme.FieldCert">
        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:theme="@style/Theme.FieldCert"
            android:windowSoftInputMode="adjustResize">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

        <provider
            android:name="androidx.core.content.FileProvider"
            android:authorities="{package}.fileprovider"
            android:exported="false"
            android:grantUriPermissions="true">
            <meta-data
                android:name="android.support.FILE_PROVIDER_PATHS"
                android:resource="@xml/file_paths" />
        </provider>
    </application>
</manifest>
"""
    (target_dir / "app/src/main/AndroidManifest.xml").write_text(manifest)

    # 7. Resources: strings.xml, colors.xml, themes.xml, file_paths.xml
    res_dir = target_dir / "app/src/main/res"
    (res_dir / "values").mkdir(parents=True, exist_ok=True)
    (res_dir / "xml").mkdir(parents=True, exist_ok=True)

    (res_dir / "values/strings.xml").write_text(f"""<resources>
    <string name="app_name">{title or name}</string>
</resources>
""")

    (res_dir / "values/themes.xml").write_text("""<resources>
    <style name="Theme.FieldCert" parent="android:Theme.Material.NoActionBar">
        <item name="android:statusBarColor">#0A0E14</item>
        <item name="android:navigationBarColor">#0A0E14</item>
    </style>
</resources>
""")

    (res_dir / "xml/file_paths.xml").write_text("""<?xml version="1.0" encoding="utf-8"?>
<paths>
    <cache-path name="reports" path="reports/" />
    <cache-path name="photos" path="photos/" />
    <files-path name="internal_reports" path="reports/" />
</paths>
""")

    # 8. .gitignore
    (target_dir / ".gitignore").write_text("""*.iml
.gradle
/local.properties
/.idea/
.DS_Store
/build
/captures
.externalNativeBuild
.cxx
*.apk
*.aab
config/keystore.jks
keystore.properties
.pipeline/secrets.json
""")

    # 9. distribution/whatsnew/whatsnew-en-US
    (target_dir / "distribution/whatsnew").mkdir(parents=True, exist_ok=True)
    (target_dir / "distribution/whatsnew/whatsnew-en-US").write_text(
        f"Initial release of {title or name}: local-first safety checklists, photo markup, and instant PDF certificates."
    )

    # 10. Generate Keystore
    ks_path = target_dir / "config/keystore.jks"
    spw, kpw = generate_keystore(ks_path, alias="upload")
    (target_dir / "keystore.properties").write_text(f"""storeFile=config/keystore.jks
storePassword={spw}
keyAlias=upload
keyPassword={kpw}
""")

    # 11. Pipeline config
    pdir = pipeline_dir(target_dir)
    pdir.mkdir(parents=True, exist_ok=True)

    profile = {
        "name": name,
        "profile_schema": 2,
        "repo_slug": f"bhanuagrawal/{name}",
        "main_branch": "main",
        "src_root": f"app/src/main/java/{package.replace('.', '/')}",
        "version_file": "app/build.gradle",
        "tag_prefix": "v",
        "whatsnew_file": "distribution/whatsnew/whatsnew-en-US",
        "whatsnew_max_chars": 500,
        "whatsnew_curated": False,
        "ci_workflow": "release.yml",
        "ci_trigger": "tag",
        "java_home": "/Applications/Android Studio.app/Contents/jbr/Contents/Home",
        "verify": {
            "compile": ":app:compileDebugKotlin",
            "test": ":app:testDebugUnitTest"
        },
        "allowed_paths": [
            "app/src/main/java/**",
            "app/src/main/res/**",
            "distribution/whatsnew/**",
            "docs/**"
        ],
        "shared_components_dir": f"app/src/main/java/{package.replace('.', '/')}/ui/components",
        "components_dir": f"app/src/main/java/{package.replace('.', '/')}/ui/components",
        "theme_dir": f"app/src/main/java/{package.replace('.', '/')}/ui/theme",
        "motion_tokens": [
            "Animatable",
            "AnimatedContent",
            "AnimatedVisibility",
            "animateColorAsState",
            "animateContentSize",
            "animateDpAsState",
            "animateFloatAsState"
        ],
        "target_name_re": r"Screen|Sheet|Dialog|Menu|Banner|Overlay|Picker|Bar",
        "skip_name_re": r"^(Test|Theme|Color|Type|Preview|Demo|Token)",
        "conventions": [
            str(Path(__file__).parent.parent / "skills/android-code/SKILL.md")
        ],
        "targets": [],
        "discovery_draft": [],
        "goals": {
            "vision": f"{title or name} is a local-first safety and compliance inspection app for trade professionals.",
            "milestones": [
                {
                    "id": "m1-core-loop",
                    "kind": "milestone",
                    "title": "Complete Inspection & Generate Official PDF Offline",
                    "decided_by": "owner",
                    "status": "active",
                    "why": "The primary pain point: technicians spending weekend hours typing reports. A complete offline job -> checklist -> photos -> signature -> PDF export solves it immediately.",
                    "outcome": "A technician can finish an on-site inspection and share an official signed PDF certificate in under 2 minutes completely offline.",
                    "measures": [
                        {"text": "Offline inspection job creation, execution, and PDF export completes end-to-end without network access"}
                    ],
                    "children": []
                }
            ]
        },
        "ci": {
            "module": "app",
            "package_name": package,
            "workflow": "release.yml",
            "trigger": "tag",
            "bundle_tasks": ":app:bundleRelease",
            "release_files": "app/build/outputs/bundle/release/app-release.aab"
        }
    }
    save_json(pdir / "profile.json", profile)

    state = {
        "runs": [],
        "shipped": [],
        "queue": [],
        "goal_log": [],
        "tree_changes": [],
        "alignment_log": [],
        "release_candidates": [],
        "last_run": {},
        "created": now_iso()
    }
    save_json(pdir / "state.json", state)
    (pdir / "tasks").mkdir(parents=True, exist_ok=True)

    # 12. Render canonical CI
    ci.write(target_dir)

    # 13. PRD & Design Docs
    docs_dir = target_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "PRD.md").write_text(f"""# {title or name} — Product Requirements Document

## Overview
{description or 'A local-first compliance & safety inspection app for technicians and inspectors.'}

## Target Users
- Commercial Fire Safety Inspectors
- Residential HVAC Technicians
- Plumbers & Electricians
- Roofing / Insurance Adjusters

## Core MVP Features
1. **Inspection Jobs Management**: Create, edit, and organize inspection records by site and client.
2. **Offline Dynamic Checklists**: Multi-trade checklists with pass/fail/warning statuses and notes.
3. **HVAC Reactive Math**: Automated Delta-T calculation ($\Delta T = T_{{return}} - T_{{supply}}$) with pass/fail threshold indication.
4. **Photo Evidence & Annotation**: In-app photo attachment with canvas drawing (defect markup, arrows) and GPS/timestamp verification.
5. **Vector Digital Signatures**: Smooth on-screen vector signature capture for inspector and client.
6. **Instant Offline PDF Generation**: Production-ready, legally compliant PDF certificate exportable via Android share sheet.
""")

    (docs_dir / "design.md").write_text("""# FieldCert Design System — Dark Industrial Tactical

## Brand Identity
FieldCert is built for tradespeople working in demanding environments: direct bright sunlight on roofs, dim basements, and dusty boiler rooms.
The UI is high-contrast, tactile, and dark-first with electric emerald and safety amber accents.

## Colors
- **Background**: `#0A0E14` (Deep Obsidian)
- **Surface**: `#161F2E` (Tactical Slate)
- **Surface High**: `#1E293B` (Elevated Card)
- **Primary Accent**: `#00E676` (Electric Emerald / Pass)
- **Warning Accent**: `#FFB703` (Safety Amber / Warning)
- **Error Accent**: `#FF3D71` (Hazard Coral / Fail)
- **On Surface**: `#FFFFFF` (Crisp White)
- **On Surface Muted**: `#94A3B8` (Slate Grey)

## Typography
- **Headlines & Values**: Space Grotesk / JetBrains Mono (precision, industrial feel)
- **Body & Labels**: Inter (high legibility at small sizes)
""")

    # 14. AGENTS.md for the project
    (target_dir / "AGENTS.md").write_text(f"""# {name} — Project Guidelines

This project is driven by the dev-team orchestrator.
Standard Android Jetpack Compose application.
Package: `{package}`
Local-first, offline-first architecture.
""")

    return target_dir


def setup_git_and_repo(target_dir: Path, name: str) -> None:
    """Initialize git repository, commit initial scaffold, create GitHub repo, and set secrets."""
    subprocess.run(["git", "init", "-b", "main"], cwd=target_dir, check=True)
    subprocess.run(["git", "add", "-A"], cwd=target_dir, check=True)
    subprocess.run(["git", "commit", "-m", f"feat: initial scaffold for {name}"], cwd=target_dir, check=True)

    # Check gh
    user_res = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True)
    if user_res.returncode == 0 and user_res.stdout.strip():
        owner = user_res.stdout.strip()
        repo_slug = f"{owner}/{name}"
        # Create GitHub repo
        print(f"Creating GitHub repository {repo_slug}...")
        create_res = subprocess.run(["gh", "repo", "create", repo_slug, "--private", "--source", "."],
                                    cwd=target_dir, capture_output=True, text=True)
        if create_res.returncode == 0:
            print(f"Repository created: {repo_slug}")
        else:
            print(f"Note: gh repo create returned {create_res.stderr.strip()}")

        # Read keystore and props
        ks_file = target_dir / "config/keystore.jks"
        props_file = target_dir / "keystore.properties"
        if ks_file.exists() and props_file.exists():
            ks_b64 = base64.b64encode(ks_file.read_bytes()).decode("ascii")
            props = dict(line.strip().split("=", 1) for line in props_file.read_text().splitlines() if "=" in line)
            
            subprocess.run(["gh", "secret", "set", "RELEASE_KEYSTORE_BASE64", "-R", repo_slug, "--body", ks_b64], check=False)
            subprocess.run(["gh", "secret", "set", "KEYSTORE_PASSWORD", "-R", repo_slug, "--body", props.get("storePassword", "")], check=False)
            subprocess.run(["gh", "secret", "set", "KEY_ALIAS", "-R", repo_slug, "--body", props.get("keyAlias", "upload")], check=False)
            subprocess.run(["gh", "secret", "set", "KEY_PASSWORD", "-R", repo_slug, "--body", props.get("keyPassword", "")], check=False)
            print("Configured keystore secrets in GitHub repository.")

        # Play service account if available
        sa_path = Path.home() / ".config/devteam/play-service-account.json"
        if sa_path.exists():
            subprocess.run(["gh", "secret", "set", "PLAY_SERVICE_ACCOUNT_JSON", "-R", repo_slug, "--body", sa_path.read_text()], check=False)
            print("Configured PLAY_SERVICE_ACCOUNT_JSON secret in GitHub repository.")

        # Push to origin
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=target_dir, check=False)
