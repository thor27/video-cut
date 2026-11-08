/**
 * Video Cut - Static Website Script
 * Handles OS detection (Ubuntu Snap recommendation), screenshot lightbox, and mobile navigation.
 */

document.addEventListener('DOMContentLoaded', () => {
  const version = document.body.dataset.version || '0.3.0';
  const appImageUrl = `https://github.com/thor27/video-cut/releases/download/v${version}/VideoCut-${version}-x86_64.AppImage`;
  const snapStoreUrl = 'https://snapcraft.io/video-cut';
  const releasesUrl = 'https://github.com/thor27/video-cut/releases';

  /* --------------------------------------------------------------------------
     1. OS / User-Agent Detection (Ubuntu -> Snap Store primary recommendation)
     -------------------------------------------------------------------------- */
  const userAgent = navigator.userAgent || '';
  const isUbuntu = /Ubuntu/i.test(userAgent);

  const primaryBtn = document.getElementById('primary-download-btn');
  const secondaryHint = document.getElementById('secondary-hint');
  const secondaryLinks = document.getElementById('secondary-links');
  const cardAppImage = document.getElementById('card-appimage');
  const cardSnap = document.getElementById('card-snap');

  if (isUbuntu) {
    // Configure primary button for Ubuntu
    if (primaryBtn) {
      primaryBtn.href = snapStoreUrl;
      primaryBtn.target = '_blank';
      primaryBtn.rel = 'noopener';

      // Snapcraft icon + subtitle
      primaryBtn.innerHTML = `
        <svg class="icon btn-icon" viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true">
          <path d="M8.17 11.335a.106.106 0 0 0-.173.022L1.754 23.466a.105.105 0 0 0 .032.133c.04.029.101.027.138-.012l8.89-9.11a.107.107 0 0 0 .005-.144l-2.649-3Zm9.76-3.519L.146.39C.041.346-.047.478.028.56l12.034 12.874a.11.11 0 0 0 .075.034.102.102 0 0 0 .075-.03L17.96 7.99a.106.106 0 0 0-.032-.174Zm6.047.547-2.188-4.405a.21.21 0 0 0-.189-.117h-8.77a.212.212 0 0 0-.08.408l10.96 4.405a.211.211 0 0 0 .268-.29z"/>
        </svg>
        <span class="btn-text-group">
          <span class="btn-main-text">Install from Snap Store</span>
          <span class="btn-sub-text">Recommended for Ubuntu · One-click install</span>
        </span>
      `;
    }

    // Configure secondary links for Ubuntu: offer AppImage and Flatpak as quick alternatives
    if (secondaryHint && secondaryLinks) {
      secondaryHint.textContent = 'Also available as:';
      secondaryLinks.innerHTML = `
        <a href="${appImageUrl}" class="secondary-link">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" class="inline-icon" aria-hidden="true">
            <path d="M1.64 0C.735 0 0 .736 0 1.64v20.72C0 23.265.736 24 1.64 24h20.72c.904 0 1.64-.736 1.64-1.64V1.64C24 .735 23.264 0 22.36 0Zm8.56 2.42h3.6v3.6h1.912L12 10.22l-3.713-4.2H10.2Zm1.331 8.4h1.313c.103 0 .169.177.169.394v1.368c.417.096.81.266 1.162.488l.975-.975c.153-.153.32-.223.394-.15l.918.919c.074.073.004.24-.15.393l-.975.975c.223.352.393.745.488 1.163h1.369c.217 0 .394.065.394.169v1.312c0 .104-.177.169-.394.169h-1.369a3.74 3.74 0 0 1-.487 1.162l.974.975c.154.154.224.32.15.394l-.918.919c-.074.073-.24.003-.394-.15l-.975-.975a3.74 3.74 0 0 1-1.163.487v1.37c0 .216-.065.393-.168.393H11.53c-.103 0-.169-.177-.169-.394v-1.369a3.74 3.74 0 0 1-1.162-.487l-.975.975c-.153.153-.32.223-.394.15l-.918-.919c-.074-.073-.004-.24.15-.394l.974-.975a3.74 3.74 0 0 1-.487-1.162H7.181c-.217 0-.393-.065-.393-.169v-1.312c0-.104.176-.17.393-.17H8.55a3.74 3.74 0 0 1 .487-1.162l-.974-.975c-.154-.153-.224-.32-.15-.393l.918-.92c.074-.072.24-.002.394.15l.975.976a3.74 3.74 0 0 1 1.163-.488v-1.368c0-.217.065-.394.168-.394zm.656 3.731c-.917 0-1.668.752-1.668 1.669s.751 1.669 1.669 1.669c.917 0 1.668-.752 1.668-1.67 0-.916-.751-1.668-1.669-1.668z"/>
          </svg>
          AppImage (v${version})
        </a>
        <span class="divider">·</span>
        <a href="#card-flatpak" class="secondary-link">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" class="inline-icon" aria-hidden="true">
            <path d="M12 0c-.556 0-1.111.144-1.61.432l-7.603 4.39a3.217 3.217 0 0 0-1.61 2.788v8.78c0 1.151.612 2.212 1.61 2.788l7.603 4.39a3.217 3.217 0 0 0 3.22 0l7.603-4.39a3.217 3.217 0 0 0 1.61-2.788V7.61a3.217 3.217 0 0 0-1.61-2.788L13.61.432A3.218 3.218 0 0 0 12 0Zm0 2.358c.15 0 .299.039.431.115l7.604 4.39c.132.077.24.187.315.316L12 12v9.642a.863.863 0 0 1-.431-.116l-7.604-4.39a.866.866 0 0 1-.431-.746V7.61c0-.153.041-.302.116-.43L12 12Z"/>
          </svg>
          Flatpak
        </a>
        <span class="divider">·</span>
        <a href="#download" class="secondary-link">
          Other packages &rarr;
        </a>
      `;
    }

    // Adjust card highlights
    if (cardAppImage && cardSnap) {
      cardAppImage.classList.remove('featured-card');
      cardSnap.classList.add('featured-card');
      const snapBadge = cardSnap.querySelector('.card-badge');
      if (snapBadge) {
        snapBadge.textContent = 'Recommended on Ubuntu';
      }
    }
  }

  /* --------------------------------------------------------------------------
     2. Lightbox Modal for Screenshots
     -------------------------------------------------------------------------- */
  const lightbox = document.getElementById('lightbox-modal');
  const lightboxImg = document.getElementById('lightbox-image');
  const lightboxCap = document.getElementById('lightbox-caption');
  const lightboxClose = document.getElementById('lightbox-close');
  const lightboxBackdrop = document.getElementById('lightbox-backdrop');

  const openLightbox = (src, caption) => {
    if (!lightbox || !lightboxImg) return;
    lightboxImg.src = src;
    lightboxImg.alt = caption || 'Screenshot full size';
    if (lightboxCap) lightboxCap.textContent = caption || '';
    lightbox.classList.add('active');
    lightbox.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
  };

  const closeLightbox = () => {
    if (!lightbox) return;
    lightbox.classList.remove('active');
    lightbox.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
  };

  document.querySelectorAll('.gallery-card').forEach(card => {
    card.addEventListener('click', () => {
      const src = card.dataset.img;
      const caption = card.dataset.caption;
      if (src) openLightbox(src, caption);
    });
  });

  if (lightboxClose) lightboxClose.addEventListener('click', closeLightbox);
  if (lightboxBackdrop) lightboxBackdrop.addEventListener('click', closeLightbox);

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && lightbox && lightbox.classList.contains('active')) {
      closeLightbox();
    }
  });

  /* --------------------------------------------------------------------------
     3. Mobile Navigation Menu Toggle
     -------------------------------------------------------------------------- */
  const navToggle = document.getElementById('nav-toggle');
  const navMenu = document.getElementById('nav-menu');

  if (navToggle && navMenu) {
    navToggle.addEventListener('click', () => {
      const isOpen = navMenu.classList.toggle('open');
      navToggle.setAttribute('aria-expanded', String(isOpen));
    });

    // Close menu when clicking navigation links on mobile
    navMenu.querySelectorAll('.nav-link').forEach(link => {
      link.addEventListener('click', () => {
        navMenu.classList.remove('open');
        navToggle.setAttribute('aria-expanded', 'false');
      });
    });
  }
  /* --------------------------------------------------------------------------
     4. Theme Management (Browser Preference & Manual Toggle)
     -------------------------------------------------------------------------- */
  const themeToggleBtn = document.getElementById('theme-toggle-btn');
  const prefersDarkMediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

  const heroImg = document.getElementById('hero-img');
  const simplicityImg = document.getElementById('feat-simplicity-img');
  const cropImg = document.getElementById('feat-crop-img');
  const imageImg = document.getElementById('feat-image-img');
  const overlayImg = document.getElementById('feat-overlay-img');

  const updateImagesForTheme = (isDark) => {
    // When dark theme: hero, crop, mixed media, overlay are dark; simplicity is light
    // When light theme: hero, crop, mixed media, overlay are light; simplicity is dark
    if (heroImg) {
      heroImg.src = isDark ? 'assets/screenshot_hero_dark.png' : 'assets/screenshot_hero_light.png';
      const pic = heroImg.parentElement;
      const src = pic ? pic.querySelector('source') : null;
      if (src) src.srcset = isDark ? 'assets/screenshot_hero_dark.png' : 'assets/screenshot_hero_light.png';
    }
    if (simplicityImg) {
      simplicityImg.src = isDark ? 'assets/screenshot_simplicity_light.png' : 'assets/screenshot.png';
      const pic = simplicityImg.parentElement;
      const src = pic ? pic.querySelector('source') : null;
      if (src) src.srcset = isDark ? 'assets/screenshot_simplicity_light.png' : 'assets/screenshot.png';
    }
    if (cropImg) {
      cropImg.src = isDark ? 'assets/screenshot_crop_rotate_dark.png' : 'assets/screenshot_crop_rotate_light.png';
      const pic = cropImg.parentElement;
      const src = pic ? pic.querySelector('source') : null;
      if (src) src.srcset = isDark ? 'assets/screenshot_crop_rotate_dark.png' : 'assets/screenshot_crop_rotate_light.png';
    }
    if (imageImg) {
      imageImg.src = isDark ? 'assets/screenshot_image_import_dark.png' : 'assets/screenshot_image_import_light.png';
      const pic = imageImg.parentElement;
      const src = pic ? pic.querySelector('source') : null;
      if (src) src.srcset = isDark ? 'assets/screenshot_image_import_dark.png' : 'assets/screenshot_image_import_light.png';
    }
    if (overlayImg) {
      overlayImg.src = isDark ? 'assets/screenshot_overlay_dark.png' : 'assets/screenshot_overlay_light.png';
      const pic = overlayImg.parentElement;
      const src = pic ? pic.querySelector('source') : null;
      if (src) src.srcset = isDark ? 'assets/screenshot_overlay_dark.png' : 'assets/screenshot_overlay_light.png';
    }

    if (themeToggleBtn) {
      themeToggleBtn.setAttribute('aria-label', isDark ? 'Switch to light theme' : 'Switch to dark theme');
      themeToggleBtn.innerHTML = isDark
        ? '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>'
        : '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>';
    }
  };

  const applyTheme = (isDark, save = false) => {
    document.documentElement.dataset.theme = isDark ? 'dark' : 'light';
    updateImagesForTheme(isDark);
    if (save) {
      localStorage.setItem('vc-theme', isDark ? 'dark' : 'light');
    }
  };

  const savedTheme = localStorage.getItem('vc-theme');
  if (savedTheme) {
    applyTheme(savedTheme === 'dark', false);
  } else {
    // Defaults to browser theme preference
    updateImagesForTheme(prefersDarkMediaQuery.matches);
  }

  prefersDarkMediaQuery.addEventListener('change', (e) => {
    if (!localStorage.getItem('vc-theme')) {
      updateImagesForTheme(e.matches);
    }
  });

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener('click', () => {
      const isCurrentlyDark = document.documentElement.dataset.theme
        ? document.documentElement.dataset.theme === 'dark'
        : prefersDarkMediaQuery.matches;
      applyTheme(!isCurrentlyDark, true);
    });
  }
});
