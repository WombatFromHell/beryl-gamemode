{
  description = "ProtonFetcher - Reproducible Python zipapp build environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = {
    self,
    nixpkgs,
    flake-utils,
  }:
    flake-utils.lib.eachDefaultSystem (
      system: let
        pkgs = nixpkgs.legacyPackages.${system};
        # Read Python version from .python-version file (strip newline and dot)
        pythonVersion = builtins.replaceStrings ["\n"] [""] (builtins.readFile ./.python-version);
        pythonAttr = builtins.replaceStrings ["."] [""] pythonVersion;
        python = pkgs."python${pythonAttr}";
      in {
        devShells.default = pkgs.mkShell {
          name = "protonfetcher";

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
            python
            rsync
            util-linux
            uv
            which
            zip
          ];

          shellHook = ''
            export PYTHON=${python}/bin/python3

            echo "ProtonFetcher development environment loaded"
            echo "Python: $(python --version)"
            echo ""
            echo "Build with: make build"
            echo "The Makefile sets SOURCE_DATE_EPOCH for reproducible builds"
          '';
        };
      }
    );
}
