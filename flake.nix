{
  description = "Beryl Gamemode - Reproducible Python zipapp build environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = {
    self,
    nixpkgs,
    pyproject-nix,
    uv2nix,
    pyproject-build-systems,
  }: let
    # Read version from pyproject.toml as source of truth
    version = let
      content = builtins.readFile ./pyproject.toml;
      lines = builtins.split "\n" content;
      filtered = builtins.filter (line: builtins.isString line && (builtins.substring 0 9 line) == "version =") lines;
      match = builtins.match ".*version = \"([^\"]+)\".*" (builtins.head filtered);
    in
      if match != null
      then builtins.head match
      else throw "Version not found in pyproject.toml";
    epoch = 1;
    forAllSystems = nixpkgs.lib.genAttrs ["x86_64-linux" "aarch64-linux"];

    # Read Python version from .python-version (e.g. "3.13" -> "python313")
    pyVerRaw = builtins.replaceStrings ["\n"] [""] (builtins.readFile ./.python-version);
    pyVerAttr = "python" + builtins.replaceStrings ["."] [""] pyVerRaw;

    mkPkgs = system: import nixpkgs {inherit system;};
    python = pkgs: pkgs.${pyVerAttr};

    # Load workspace from uv.lock and create overlay for reproducible Python packages
    workspace = uv2nix.lib.workspace.loadWorkspace {
      workspaceRoot = ./.;
    };
    projectOverlay = workspace.mkPyprojectOverlay {
      sourcePreference = "wheel";
    };

    mkPythonSet = system: let
      pkgs' = mkPkgs system;
    in
      (pkgs'.callPackage pyproject-nix.build.packages {
        python = python pkgs';
      }).overrideScope (nixpkgs.lib.composeManyExtensions [
        pyproject-build-systems.overlays.wheel
        projectOverlay
      ]);

    # Reproducible zipapp derivation — mirrors `make build` semantics
    mkZipapp = system: let
      pkgs = mkPkgs system;
      py = python pkgs;
      pythonSet = mkPythonSet system;
      venv = pythonSet.mkVirtualEnv "gamemode-env" [];

      # Inject version into __version__.py
      src = pkgs.stdenvNoCC.mkDerivation {
        name = "gamemode-src";
        buildInputs = [pkgs.gnused];
        phases = ["installPhase"];
        installPhase = ''
          mkdir -p $out
          cp -r ${./src} staging
          chmod -R u+w staging
          sed -i 's/^__version__ = .*/__version__ = "${version}"/' \
            "staging/gamemode/__version__.py"
          cp -r staging/* $out/
        '';
      };
    in
      pkgs.stdenvNoCC.mkDerivation {
        name = "gamemode.pyz";

        nativeBuildInputs = with pkgs; [
          coreutils
          findutils
          gnused
          zip
          py
        ];

        # Use the uv2nix-built virtualenv as the Python environment
        # (provides deterministic, reproducible dependency resolution)
        PYTHON = "${py}/bin/python3";

        buildPhase = ''
          # Create staging directory
          mkdir -p staging
          cp -r ${src}/* staging

          # Create __main__.py entry point
          echo "from entry import main; main()" > staging/__main__.py

          # Normalize permissions and timestamps for determinism
          # (mirrors SOURCE_DATE_EPOCH + 0o644 file mode)
          chmod -R u+w staging
          find staging -exec touch -d "@${builtins.toString epoch}" {} +

          # Build deterministic zip: sorted file list, no extra attributes
          (cd staging && find . -type f | LC_ALL=C sort | zip -X -q -@ archive.zip)

          # Prepend shebang to create executable pyz
          echo '#!/usr/bin/env python3' > $out
          cat staging/archive.zip >> $out
          chmod +x $out
        '';

        dontUnpack = true;
        dontInstall = true;
      };
  in {
    packages = forAllSystems (system: {
      default = mkZipapp system;
    });

    devShells = forAllSystems (system: let
      pkgs = mkPkgs system;
      pythonSet = mkPythonSet system;
      venv = pythonSet.mkVirtualEnv "gamemode-dev" [];
    in
      pkgs.mkShell {
        name = "beryl-gamemode";

        packages = with pkgs;
          [
            bashInteractive
            coreutils
            findutils
            gawk
            git
            gnugrep
            gnused
            gnutar
            jq
            less
            prettier
            rsync
            util-linux
            uv
            which
            zip
          ]
          ++ [
            # Drop-in replacement for `uv run` — deterministic virtualenv from uv2nix
            venv
          ];

        shellHook = ''
          export PYTHONPATH="${venv}":$PYTHONPATH

          echo "Beryl Gamemode development environment loaded"
          echo "Python: $(${python pkgs}/bin/python3 --version)"
          echo ""
          echo "Build with: make build  (local)"
          echo "Nix build: nix build  (reproducible)"
        '';

        # Make the venv's Python the default
        VIRTUAL_ENV = "${venv}";
        PATH = "${venv}/bin:$PATH";
      });
  };
}
