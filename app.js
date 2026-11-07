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
        <svg class="icon btn-icon" viewBox="0 0 24 24" width="22" height="22" fill="currentColor">
          <path d="M11.64 0l-7.2 4.16 8.35 4.82v9.64l4.16-2.4V4.82L11.64 0zM3.46 5.86L0 7.85l7.2 4.16 3.46-2-7.2-4.15zm17.08 6.32l-3.46 2 7.2 4.16 3.46-2-7.2-4.16zM11.64 12l-7.2 4.16 7.2 4.16 7.2-4.16-7.2-4.16z"/>
        </svg>
        <span class="btn-text-group">
          <span class="btn-main-text">Install from Snap Store</span>
          <span class="btn-sub-text">Recommended for Ubuntu · One-click install</span>
        </span>
      `;
    }

    // Configure secondary links for Ubuntu: offer AppImage as the quick alternative
    if (secondaryHint && secondaryLinks) {
      secondaryHint.textContent = 'Alternative:';
      secondaryLinks.innerHTML = `
        <a href="${appImageUrl}" class="secondary-link">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" class="inline-icon">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="7 10 12 15 17 10"></polyline>
            <line x1="12" y1="15" x2="12" y2="3"></line>
          </svg>
          Download portable AppImage (v${version})
        </a>
        <span class="divider">·</span>
        <a href="${releasesUrl}" class="secondary-link" target="_blank" rel="noopener">
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
