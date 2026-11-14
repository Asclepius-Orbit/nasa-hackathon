// electron-builder afterPack hook: flips Electron fuses on the packaged binary.
const path = require('node:path');
const { flipFuses, FuseVersion, FuseV1Options } = require('@electron/fuses');

/** @param {import('electron-builder').AfterPackContext} context */
module.exports = async function afterPack(context) {
  const { appOutDir, packager, electronPlatformName } = context;
  if (electronPlatformName !== 'win32') {
    throw new Error(`Fuses are only configured for win32, got ${electronPlatformName}`);
  }
  const exe = path.join(appOutDir, `${packager.appInfo.productFilename}.exe`);
  await flipFuses(exe, {
    version: FuseVersion.V1,
    [FuseV1Options.RunAsNode]: false,
    [FuseV1Options.EnableCookieEncryption]: true,
    [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
    [FuseV1Options.EnableNodeCliInspectArguments]: false,
    [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
    [FuseV1Options.OnlyLoadAppFromAsar]: true,
    [FuseV1Options.GrantFileProtocolExtraPrivileges]: false,
  });
};
