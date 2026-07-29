{
  description = "Beryl Gamemode - Reproducible Python zipapp build environment";
  inputs = {
    nixpkgs.url = "https://flakehub.com/f/DeterminateSystems/nixpkgs-26.05-chilled/0.1";
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
    nixpkgs,
    pyproject-nix,
    uv2nix,
    pyproject-build-systems,
    ...
  }: let
    system = "x86_64-linux";

    version = let
      m = builtins.match ".*\nversion = \"([^\"]+)\".*" ("\n" + builtins.readFile ./pyproject.toml);
    in
      if m != null
      then builtins.head m
      else throw "Version not found in pyproject.toml";

    epoch = 1;

    pyVerRaw = builtins.replaceStrings ["\n"] [""] (builtins.readFile ./.python-version);
    pyVerAttr = "python" + builtins.replaceStrings ["."] [""] pyVerRaw;

    pkgs = import nixpkgs {inherit system;};
    py = pkgs.${pyVerAttr};

    workspace = uv2nix.lib.workspace.loadWorkspace {
      workspaceRoot = ./.;
    };
    projectOverlay = workspace.mkPyprojectOverlay {
      sourcePreference = "wheel";
    };
    pythonSet =
      (pkgs.callPackage pyproject-nix.build.packages {
        python = py;
      }).overrideScope (nixpkgs.lib.composeManyExtensions [
        pyproject-build-systems.overlays.wheel
        projectOverlay
      ]);

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

    zipapp = pkgs.stdenvNoCC.mkDerivation {
      name = "gamemode.pyz";
      nativeBuildInputs = [
        pkgs.coreutils
        pkgs.findutils
        pkgs.gnused
        pkgs.zip
        py
      ];
      dontUnpack = true;
      dontInstall = true;
      buildPhase = ''
        mkdir -p staging
        cp -r ${src}/* staging
        echo "from entry import main; main()" > staging/__main__.py

        chmod -R u+w staging
        find staging -type f -exec chmod 644 {} +
        find staging -type d -exec chmod 755 {} +
        find staging -exec touch -d "@${toString epoch}" {} +

        (cd staging && find . -type f | LC_ALL=C sort | zip -X -q -@ archive.zip)

        echo '#!${py}/bin/python3' > $out
        cat staging/archive.zip >> $out
        chmod +x $out
      '';
    };
    # ponytail: zipapp bundles source only, no third-party deps. If runtime
    # deps are ever needed, vendor pythonSet's site-packages into `staging`
    # before zipping rather than building a throwaway venv for it.
  in {
    packages.${system}.default = zipapp;

    devShells.${system}.default = let
      venv = pythonSet.mkVirtualEnv "gamemode-dev" {};
    in
      pkgs.mkShell {
        name = "beryl-gamemode";
        packages = with pkgs; [
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
        ];
        shellHook = ''
          export VIRTUAL_ENV="${venv}"
          export PATH="${venv}/bin:$PATH"
          echo "Beryl Gamemode development environment loaded"
          echo "Python: $(${py}/bin/python3 --version)"
          echo ""
          echo "Build with: make build  (local)"
          echo "Nix build: nix build  (reproducible)"
        '';
      };
  };
}
