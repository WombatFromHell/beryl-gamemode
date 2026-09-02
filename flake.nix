{
  description = "Beryl Gamemode - Reproducible Python zipapp build environment";

  inputs = {
    nixpkgs.url = "https://flakehub.com/f/DeterminateSystems/nixpkgs-26.05-chilled/0.1";
  };

  outputs = {
    self,
    nixpkgs,
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

    # zipapp bundles source only, no third-party deps. If runtime deps are
    # ever needed, vendor site-packages into `staging` before zipping.
    zipapp = pkgs.stdenvNoCC.mkDerivation {
      name = "gamemode.pyz";
      nativeBuildInputs = [pkgs.coreutils pkgs.findutils pkgs.gnused pkgs.zip];
      dontUnpack = true;
      dontInstall = true;
      buildPhase = ''
        mkdir -p staging
        cp -r ${./src}/. staging
        chmod -R u+w staging

        sed -i 's/^__version__ = .*/__version__ = "${version}"/' \
          "staging/gamemode/__version__.py"
        echo "from entry import main; main()" > staging/__main__.py

        find staging -type f -exec chmod 644 {} +
        find staging -type d -exec chmod 755 {} +
        find staging -exec touch -d "@${toString epoch}" {} +

        (cd staging && find . -type f | LC_ALL=C sort | zip -X -q -@ archive.zip)

        echo '#!/usr/bin/python3' > $out
        cat staging/archive.zip >> $out
        chmod +x $out
      '';
    };

    # $bin wrapper so the pyz is installable on $PATH by home-manager / NixOS
    # (the raw pyz output is a file, not a directory).
    gamemode =
      pkgs.runCommand "gamemode" {
        passthru = {inherit zipapp;};
      } ''
        mkdir -p $out/bin
        cp ${zipapp} $out/bin/gamemode
        chmod +x $out/bin/gamemode
      '';
  in {
    packages.${system} = {
      default = zipapp;
      inherit gamemode;
    };

    homeModules.default = {pkgs, ...}: {
      home.packages = [self.packages.${pkgs.system}.gamemode];
    };
    nixosModules.default = {pkgs, ...}: {
      environment.systemPackages = [self.packages.${pkgs.system}.gamemode];
    };

    devShells.${system}.default = pkgs.mkShell {
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
        py
      ];
      shellHook = ''
        echo "Beryl Gamemode development environment loaded"
        echo "Python: $(${py}/bin/python3 --version)"
        echo ""
        echo "Build with: make build  (local)"
        echo "Nix build: nix build  (reproducible)"
      '';
    };
  };
}
