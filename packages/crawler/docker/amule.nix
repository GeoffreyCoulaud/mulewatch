# aMule 3.0.1 (amuled + amuleweb + ed2k), built headless from a pinned nixpkgs.
#
# Pinning nixpkgs by commit is what makes the build reproducible and the SBOM stable: every
# component in the closure keeps a name and a version, which is the whole reason we package aMule
# ourselves instead of basing on a source-built third-party image.
#
# Build:  nix-build amule.nix -o /amule
let
  nixpkgs = builtins.fetchTarball {
    url = "https://github.com/NixOS/nixpkgs/archive/c7def046b9a883d46974757852106483d741586f.tar.gz";
    sha256 = "sha256-6RSEDHIWQtesQKWSu5qRai8L2h4KgCgMEfJHstW99G4=";
  };
in
{ pkgs ? import nixpkgs { } }:

with pkgs;

let
  # A GUI-less wxWidgets. --disable-gui is not an exposed option, hence the overrideAttrs:
  # --enable-mediactrl is unconditional upstream and is what pulls GStreamer in, so it goes with
  # the GUI, not alongside it. Without this the closure is 634 MB; with it, 101 MB.
  wxBase = (wxwidgets_3_2.override { withWebKit = false; withMesa = false; }).overrideAttrs (old: {
    configureFlags =
      (builtins.filter (f: f != "--enable-mediactrl" && f != "--with-nanosvg") old.configureFlags)
      ++ [ "--disable-gui" ];
    buildInputs = [ zlib pcre2 expat curl ];
  });

  # The pname is load-bearing: Syft derives the CPE from the package name, and the daemon/httpServer
  # override renames the derivation to amule-web-daemon, a name the NVD does not know. Grype would
  # then report zero findings on a vulnerable aMule, silently. Forcing it back to "amule" yields
  # cpe:2.3:a:amule:amule:3.0.1 and real matches. See the spec, section 8.
  amule' = (amule.override {
    monolithic = false; enableDaemon = true; httpServer = true; wxwidgets_3_2 = wxBase;
  }).overrideAttrs (_: { pname = "amule"; });
in
amule'
