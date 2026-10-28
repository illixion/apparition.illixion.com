// Renders the download section from /release.json. All fetched values go in
// through textContent or checked href attributes, never as markup.
(function () {
  "use strict";

  var SITE = "https://apparition.illixion.com";
  var GITHUB = "https://github.com/illixion/Apparition";

  var OS_NAMES = { windows: "Windows", macos: "macOS", linux: "Linux" };
  var ARCH_NAMES = {
    x86_64: "x64", amd64: "x64", arm64: "Arm64", aarch64: "Arm64",
    universal: "Universal", x86: "x86", i686: "x86"
  };
  var APP_NAMES = {
    visionos: "Apple Vision Pro", ios: "iPhone and iPad", tvos: "Apple TV",
    macos: "Mac", watchos: "Apple Watch"
  };
  var OS_ORDER = ["macos", "windows", "linux"];

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  // Only https URLs (or same-origin relative paths for the signature files)
  // become links, so a malformed release.json can't produce javascript: hrefs.
  function safeUrl(value, allowRelative) {
    if (typeof value !== "string" || value === "") return null;
    try {
      var url = new URL(value, allowRelative ? location.origin + "/" : undefined);
      if (url.protocol === "https:") return url.href;
      if (allowRelative && url.origin === location.origin) return url.href;
    } catch (e) { /* fall through */ }
    return null;
  }

  function link(href, cls, text) {
    var a = el("a", cls, text);
    a.href = href;
    return a;
  }

  // Counts a click as a Umami event (self-hosted, stats.illixion.com). A plain listener rather
  // than data-umami-event, whose handler holds the download until the beacon returns.
  function track(node, name, data) {
    node.addEventListener("click", function () {
      if (window.umami) window.umami.track(name, data);
    });
  }

  function formatSize(bytes) {
    if (typeof bytes !== "number" || !isFinite(bytes) || bytes <= 0) return "";
    if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + " GB";
    if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + " MB";
    return Math.max(1, Math.round(bytes / 1e3)) + " KB";
  }

  function formatDate(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
    if (!m) return "";
    var d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    try {
      return new Intl.DateTimeFormat(undefined, {
        year: "numeric", month: "long", day: "numeric", timeZone: "UTC"
      }).format(d);
    } catch (e) {
      return m[0];
    }
  }

  function splitPlatform(platform) {
    var p = String(platform || "").toLowerCase();
    var i = p.indexOf("-");
    return i < 0 ? { os: p, arch: "" } : { os: p.slice(0, i), arch: p.slice(i + 1) };
  }

  function visitorOS() {
    var uad = navigator.userAgentData;
    var hint = ((uad && uad.platform) || navigator.platform || "") + " " + navigator.userAgent;
    if (/android|iphone|ipad|ipod/i.test(hint)) return null;
    if (/win/i.test(hint)) return "windows";
    if (/mac/i.test(hint)) return navigator.maxTouchPoints > 1 ? null : "macos";
    if (/linux|x11|cros/i.test(hint)) return "linux";
    return null;
  }

  function renderSoon(root) {
    var box = el("div", "soon");
    box.appendChild(el("h3", null, "First release coming soon"));
    box.appendChild(el("p", null,
      "Apparition hasn't published a build yet. Watch the project on GitHub to hear when the first one is out."));
    box.appendChild(link(GITHUB, "button quiet", "Watch on GitHub"));
    root.replaceChildren(box);
  }

  function installerButton(asset, mine) {
    var href = safeUrl(asset.urls && asset.urls[0]);
    if (!href) return null;
    var parts = splitPlatform(asset.platform);
    var osName = OS_NAMES[parts.os] || parts.os || "Download";
    var meta = [ARCH_NAMES[parts.arch] || parts.arch, formatSize(asset.size)]
      .filter(Boolean).join(", ");

    var a = link(href, "installer" + (mine ? " is-yours" : ""));
    a.appendChild(el("span", "installer-name", "Apparition for " + osName));
    if (meta) a.appendChild(el("span", "installer-meta", meta));
    if (mine) a.appendChild(el("span", "installer-meta", "Matches this computer"));
    if (asset.name) a.setAttribute("download", "");
    track(a, "download", { kind: "desktop", platform: String(asset.platform || ""), yours: !!mine });
    var li = el("li");
    li.appendChild(a);
    return li;
  }

  function copyButton(text) {
    var button = el("button", "copy", "Copy address");
    button.type = "button";
    if (!navigator.clipboard) return null;
    button.addEventListener("click", function () {
      navigator.clipboard.writeText(text).then(function () {
        button.textContent = "Copied";
        setTimeout(function () { button.textContent = "Copy address"; }, 2000);
      }, function () {
        button.textContent = "Copy failed";
      });
    });
    return button;
  }

  function versionKey(version) {
    return String(version).split("-")[0].split(".").map(function (p) { return parseInt(p, 10) || 0; });
  }

  function newerVersion(a, b) {
    var x = versionKey(a), y = versionKey(b);
    for (var i = 0; i < Math.max(x.length, y.length); i++) {
      if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0);
    }
    return false;
  }

  function installersOf(release) {
    var assets = release && Array.isArray(release.assets) ? release.assets : [];
    return assets.filter(function (a) { return a && a.kind === "installer"; });
  }

  // A release that only fixes the apps has no installers; the desktop app is then the newest
  // one that has them, which is the release's own when it carries any.
  function installerRelease(releases, release) {
    if (installersOf(release).length) return release;
    var best = null;
    releases.forEach(function (r) {
      if (r && installersOf(r).length && (!best || newerVersion(r.version, best.version))) best = r;
    });
    return best;
  }

  function renderRelease(root, release, installers) {
    var frag = document.createDocumentFragment();
    var assets = Array.isArray(release.assets) ? release.assets : [];

    var head = el("div", "release-head");
    head.appendChild(el("h3", null, "Apparition " + release.version));
    var date = formatDate(release.date);
    if (date) head.appendChild(el("p", "release-date", "Released " + date));
    frag.appendChild(head);
    // Installers: the visitor's OS first and highlighted, then the rest.
    var mine = visitorOS();
    var fromRelease = installers || release;
    installers = installersOf(fromRelease);
    var linuxPackages = installers.filter(function (a) {
      return /^linux-(x86_64|arm64)$/.test(a.platform) && (a.format === "deb" || a.format === "AppImage");
    });
    var hasLinuxFormats = linuxPackages.length > 0;
    // The Linux archive remains on GitHub for command-line use. Direct desktop downloads are
    // grouped below by architecture so format and CPU requirements are explicit.
    if (hasLinuxFormats) {
      installers = installers.filter(function (a) {
        return a.platform.indexOf("linux-") !== 0;
      });
    }
    function rank(asset) {
      var os = splitPlatform(asset.platform).os;
      if (os === mine) return -1;
      var i = OS_ORDER.indexOf(os);
      return i < 0 ? OS_ORDER.length : i;
    }
    installers.sort(function (a, b) { return rank(a) - rank(b); });
    var highlighted = false;
    var list = el("ul", "installers");
    installers.forEach(function (asset) {
      var isMine = !highlighted && mine && splitPlatform(asset.platform).os === mine;
      var item = installerButton(asset, isMine);
      if (item) {
        if (isMine) highlighted = true;
        list.appendChild(item);
      }
    });
    if (list.childNodes.length) {
      frag.appendChild(el("p", "release-lead",
        "The desktop app puts Apparition on your device, then can stay in the menu bar or tray as its companion."));
      if (fromRelease.version !== release.version) {
        frag.appendChild(el("p", "release-lead",
          "These are version " + fromRelease.version + ", the newest desktop app. Version " + release.version +
          " changes only the apps on your device, and the desktop app installs the latest of them."));
      }
      frag.appendChild(list);
    }

    if (hasLinuxFormats) {
      var linux = el("section", "linux-downloads");
      linux.setAttribute("aria-label", "Linux desktop downloads");
      linux.appendChild(el("h4", null, "Linux desktop"));
      linux.appendChild(el("p", "installer-meta", "Choose your CPU architecture, then download a .deb package or AppImage."));
      [
        { platform: "linux-x86_64", label: "x64" },
        { platform: "linux-arm64", label: "ARM64" }
      ].forEach(function (arch) {
        var packages = linuxPackages.filter(function (asset) { return asset.platform === arch.platform; });
        if (!packages.length) return;
        var group = el("div", "linux-arch");
        group.appendChild(el("h5", null, arch.label));
        var options = el("div", "linux-formats");
        ["deb", "AppImage"].forEach(function (format) {
          var asset = packages.filter(function (item) { return item.format === format; })[0];
          if (!asset) return;
          var href = safeUrl(asset.urls && asset.urls[0]);
          if (!href) return;
          var label = format === "deb" ? "Download .deb" : "Download AppImage";
          var a = link(href, "installer linux-installer");
          a.appendChild(el("span", "installer-name", label));
          var size = formatSize(asset.size);
          if (size) a.appendChild(el("span", "installer-meta", size));
          a.setAttribute("download", "");
          track(a, "download", { kind: "desktop", platform: arch.platform, format: format });
          options.appendChild(a);
        });
        group.appendChild(options);
        linux.appendChild(group);
      });
      frag.appendChild(linux);
    }

    var extra = el("div", "release-extra");

    // App builds and their sources, one per platform present.
    var apps = assets.filter(function (a) { return a && a.kind === "app"; });
    var seen = {};
    apps.forEach(function (asset) {
      var platform = String(asset.platform || "").toLowerCase();
      if (!/^[a-z0-9]+$/.test(platform) || seen[platform]) return;
      seen[platform] = true;
      var href = safeUrl(asset.urls && asset.urls[0]);
      var sourceUrl = SITE + "/source-" + platform + ".json";
      var block = el("div", "app-block");
      block.appendChild(el("h4", null, "For " + (APP_NAMES[platform] || platform)));
      if (href) {
        var p = el("p");
        var a = link(href, null, "Download the IPA");
        a.setAttribute("download", "");
        track(a, "download", { kind: "ipa", platform: platform });
        p.appendChild(a);
        var size = formatSize(asset.size);
        p.appendChild(document.createTextNode(
          (size ? " (" + size + ")" : "") + ", for AltStore, SideStore or manual install."));
        block.appendChild(p);
      }
      var row = el("div", "source-row");
      var add = link(sourceUrl, null, "Add source");
      track(add, "add-source", { platform: platform, via: "link" });
      row.appendChild(add);
      var copy = copyButton(sourceUrl);
      if (copy) {
        track(copy, "add-source", { platform: platform, via: "copy" });
        row.appendChild(copy);
      }
      block.appendChild(row);
      block.appendChild(el("p", "source-url", sourceUrl));
      extra.appendChild(block);
    });

    // Signature note.
    var sums = safeUrl(release.sums, true);
    var sig = safeUrl(release.signature, true);
    var signed = el("div", "signed");
    signed.appendChild(el("h4", null, "Every release is signed"));
    var sp = el("p");
    if (sums || sig) {
      sp.appendChild(document.createTextNode("Check your download against "));
      if (sums) sp.appendChild(link(sums, null, "SHA256SUMS"));
      if (sums && sig) sp.appendChild(document.createTextNode(" and its "));
      if (sig) sp.appendChild(link(sig, null, "signature"));
      sp.appendChild(document.createTextNode("."));
    } else {
      sp.textContent = "Checksums and the signature are published with each release.";
    }
    signed.appendChild(sp);
    extra.appendChild(signed);

    frag.appendChild(extra);
    root.replaceChildren(frag);
  }

  // Every release's notes, newest first, so a small release doesn't push a bigger one's out of sight.
  function renderChangelog(releases) {
    var section = document.getElementById("changelog");
    var list = document.getElementById("changelog-list");
    if (!section || !list) return;
    var entries = releases.filter(function (r) { return r && typeof r.version === "string"; })
      .sort(function (a, b) { return newerVersion(a.version, b.version) ? -1 : newerVersion(b.version, a.version) ? 1 : 0; });
    var frag = document.createDocumentFragment();
    entries.forEach(function (release) {
      var entry = el("article", "log-entry");
      var head = el("div", "release-head");
      head.appendChild(el("h3", null, "Apparition " + release.version));
      var date = formatDate(release.date);
      if (date) head.appendChild(el("p", "release-date", date));
      entry.appendChild(head);
      if (typeof release.notes === "string" && release.notes.trim()) {
        entry.appendChild(el("p", "release-notes", release.notes.trim()));
      }
      frag.appendChild(entry);
    });
    if (!frag.childNodes.length) return;
    list.replaceChildren(frag);
    section.hidden = false;
  }

  function load() {
    var root = document.getElementById("release");
    if (!root) return;
    fetch("/release.json", { cache: "no-cache", headers: { Accept: "application/json" } })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (!data || data.format !== "apparition-releases" || !Array.isArray(data.releases)) {
          throw new Error("Unexpected release.json");
        }
        var release = data.releases.filter(function (r) {
          return r && r.version === data.latest;
        })[0];
        if (!release) throw new Error("Latest release not listed");
        renderRelease(root, release, installerRelease(data.releases, release));
        renderChangelog(data.releases);
      })
      .catch(function () { renderSoon(root); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
