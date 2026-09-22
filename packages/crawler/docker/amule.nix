# aMule 3.1.0 (amuled + amuleapi + ed2k), built headless from a pinned nixpkgs.
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

  version = "3.1.0";

  # The pname must stay "amule": Syft builds the CPE from it, and the override below renames the
  # derivation to amule-daemon, which the NVD does not know: Grype would then find nothing.
  #
  # nixpkgs is still on 3.0.1, hence the src override. It replaces the whole fetchFromGitHub
  # rather than just `version`: the upstream expression reads `tag = finalAttrs.version` but keeps
  # the hash as a literal inside src, so bumping version alone would fetch 3.1.0 and check it
  # against 3.0.1's hash.
  amule' = (amule.override {
    monolithic = false; enableDaemon = true; httpServer = false; wxwidgets_3_2 = wxBase;
  }).overrideAttrs (old: {
    inherit version;
    pname = "amule";
    src = fetchFromGitHub {
      owner = "amule-org";
      repo = "amule";
      tag = version;
      hash = "sha256-IO0sAqCEWNsLtf7jQUHOAbCs50M13KN1vVSO2lBG7d0=";
    };
    # 3.1.0 generates sources at configure time, which the 3.0.1 expression has no reason to know
    # about.
    nativeBuildInputs = old.nativeBuildInputs ++ [ python3 ];
    # BUILD_AMULEAPI is new in 3.1.0, so it is not among the expression's own cmakeFlags.
    cmakeFlags = old.cmakeFlags ++ [ (lib.cmakeBool "BUILD_AMULEAPI" true) ];
  });
in
amule'
