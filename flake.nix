{
  description = "Jackify — Wabbajack modlist installer for Linux";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      lib = pkgs.lib;

      jackify = pkgs.python3Packages.buildPythonApplication {
        pname = "jackify";
        version = "0.6.0.1";
        pyproject = true;
        src = ./.;

        build-system = with pkgs.python3Packages; [ setuptools ];

        dependencies = with pkgs.python3Packages; [
          pyside6
          psutil
          requests
          tqdm
          pycryptodome
          pyyaml
          vdf
          packaging
          watchdog
        ];

        postInstall = ''
          install -Dm644 assets/JackifyLogo_256.png \
            $out/share/icons/hicolor/256x256/apps/jackify.png
          install -Dm644 ${pkgs.writeText "jackify.desktop" ''
            [Desktop Entry]
            Type=Application
            Name=Jackify
            Comment=Wabbajack modlist installation and configuration for Linux
            Exec=jackify %U
            Icon=jackify
            Terminal=false
            Categories=Game;Utility;
            MimeType=x-scheme-handler/jackify;
            Keywords=Wabbajack;Modlist;Mods;Proton;MO2;
          ''} $out/share/applications/jackify.desktop
        '';

        meta = {
          description = "Wabbajack modlist installer for Linux";
          homepage = "https://github.com/gastrodon/Jackify";
          license = lib.licenses.gpl3Only;
          platforms = [ "x86_64-linux" ];
          mainProgram = "jackify";
        };
      };
    in
    {
      packages.${system} = {
        default = jackify;
        jackify = jackify;
      };

      apps.${system}.default = {
        type = "app";
        program = "${jackify}/bin/jackify";
      };

      overlays.default = final: prev: {
        jackify = jackify;
      };

      nixosModules.default =
        { pkgs, ... }:
        {
          nixpkgs.overlays = [ self.overlays.default ];
          environment.systemPackages = [ pkgs.jackify ];
        };
    };
}
