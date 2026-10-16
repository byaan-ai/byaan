import jsPDF from 'jspdf';
import html2canvas from 'html2canvas';
import { saveBlobToFile, isTauriApp } from '../lib/tauri-api';
import { showToast } from '../utils/toast';
import { injectErrorCaptureScript } from '../utils/iframeErrorCapture';
import { getAccessToken } from './tokenStore';

declare global {
  interface Window {
    pdfDataReady?: boolean;
  }
}

interface PdfGenerationOptions {
  notebookId: string;
  version?: number | null;
  fileName?: string;
}

/**
 * Waits for a condition to be true with timeout
 */
async function waitForCondition(
  checkFn: () => boolean,
  timeout: number = 30000,
  interval: number = 100
): Promise<void> {
  const startTime = Date.now();

  while (!checkFn()) {
    if (Date.now() - startTime > timeout) {
      throw new Error('Timeout waiting for condition');
    }
    await new Promise(resolve => setTimeout(resolve, interval));
  }
}

/**
 * Generates a PDF from a notebook by loading it in a hidden iframe
 * and capturing the rendered content
 */
export async function generatePdfFromNotebook(
  options: PdfGenerationOptions
): Promise<string> {
  const { notebookId, version = null, fileName } = options;

  // Create hidden iframe
  // IMPORTANT: Keep on-screen (0,0) for Tauri WebView rendering, but make invisible
  const iframe = document.createElement('iframe');
  iframe.style.position = 'fixed';
  iframe.style.left = '0';
  iframe.style.top = '0';
  iframe.style.width = '1920px';
  iframe.style.height = '1080px';
  iframe.style.border = 'none';
  iframe.style.zIndex = '-9999'; // Behind everything
  iframe.style.opacity = '0'; // Invisible to user
  iframe.style.pointerEvents = 'none'; // No interaction
  document.body.appendChild(iframe);

  try {
    // Fetch compiled HTML from backend (standalone with embedded data)
    const { getBackendUrl, isTauriApp: checkTauri } = await import('../lib/tauri-api');

    // Use relative URL in browser mode
    let htmlUrl: string;
    if (checkTauri()) {
      const backendUrl = await getBackendUrl();
      htmlUrl = `${backendUrl}/api/notebooks/${notebookId}/export/compiled-html${version ? `?version=${version}` : ''}`;
    } else {
      htmlUrl = `/api/notebooks/${notebookId}/export/compiled-html${version ? `?version=${version}` : ''}`;
    }

    console.log(`[PDF] Loading notebook from: ${htmlUrl}`);

    // Build headers with authentication and tenant ID
    const headers: HeadersInit = {};

    const authToken = getAccessToken();
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }

    const tenantId = localStorage.getItem('byaan_active_tenant');
    if (tenantId) {
      headers['X-Tenant-ID'] = tenantId;
    }

    const response = await fetch(htmlUrl, { headers });
    if (!response.ok) {
      throw new Error(`Failed to fetch notebook HTML: ${response.status}`);
    }
    let htmlContent = await response.text();

    console.log(`[PDF] HTML fetched, injecting PDF data ready detection script...`);

    // Inject error capture script with PDF data ready detection
    htmlContent = injectErrorCaptureScript(htmlContent);

    console.log(`[PDF] Script injected, loading via srcdoc for same-origin access`);

    // Load HTML via srcdoc (keeps same-origin, allows contentDocument access)
    iframe.srcdoc = htmlContent;

    // Wait for iframe to load
    await new Promise<void>((resolve, reject) => {
      iframe.onload = () => resolve();
      iframe.onerror = () => reject(new Error('Failed to load notebook HTML'));

      // Timeout after 30 seconds
      setTimeout(() => reject(new Error('Timeout loading notebook HTML')), 30000);
    });

    // Content should now be accessible (same-origin)
    if (!iframe.contentDocument || !iframe.contentWindow) {
      throw new Error('Iframe content not accessible');
    }

    console.log('[PDF] HTML loaded, waiting for React to mount...');

    // Wait for React to mount (check for root element with children)
    await waitForCondition(() => {
      const root = iframe.contentDocument?.getElementById('root');
      return Boolean(root && root.children.length > 0);
    }, 15000);

    console.log('[PDF] React mounted, waiting for data to load...');

    // Wait for data loading signal
    // The dashboard should set window.pdfDataReady = true when all data is loaded
    try {
      await waitForCondition(() => {
        const isReady = iframe.contentWindow?.pdfDataReady === true;
        if (isReady) {
          console.log('[PDF] pdfDataReady flag detected!');
        }
        return isReady;
      }, 30000); // Increased timeout to 30 seconds
      console.log('[PDF] Data ready signal received');

    } catch (error) {
      // If no explicit signal, check for common dashboard elements
      console.log('[PDF] Timeout waiting for pdfDataReady, checking for content elements...');
      const doc = iframe.contentDocument;
      if (!doc) {
        throw new Error('Cannot access iframe document');
      }

      // Check for common dashboard elements
      const hasDashboard = doc.querySelector('div.dashboard, div.container, div[class*="dashboard"]');
      const hasCharts = doc.querySelector('canvas, svg');
      const hasContent = doc.querySelector('.text-2xl, h1, h2, h3, p');
      const hasDataElements = doc.querySelector('[data-chart], [data-query], [class*="chart"], [class*="Chart"]');

      if (!hasDashboard && !hasCharts && !hasContent && !hasDataElements) {
        throw new Error('No dashboard content found in iframe. Dashboard may have failed to load.');
      }

      console.log('[PDF] Found dashboard elements, continuing with capture');
    }

    console.log('[PDF] Data loaded, waiting for rendering...');

    // Wait a bit for any animations/charts to finish rendering
    // Increased wait time for complex dashboards with charts
    await new Promise(resolve => setTimeout(resolve, 3000));

    console.log('[PDF] Capturing content...');

    // Get the body element to capture
    const body = iframe.contentDocument.body;
    if (!body) {
      throw new Error('No body element found in notebook');
    }

    // Get actual content dimensions
    const dimensions = {
      width: Math.max(
        body.scrollWidth,
        body.offsetWidth,
        iframe.contentDocument.documentElement.clientWidth
      ),
      height: Math.max(
        body.scrollHeight,
        body.offsetHeight,
        iframe.contentDocument.documentElement.clientHeight
      )
    };

    console.log(`[PDF] Content dimensions: ${dimensions.width}x${dimensions.height}px`);

    // Capture content using html2canvas
    const canvas = await html2canvas(body, {
      width: dimensions.width,
      height: dimensions.height,
      windowWidth: dimensions.width,
      windowHeight: dimensions.height,
      scale: 2, // Higher quality
      useCORS: true,
      allowTaint: false,
      backgroundColor: '#ffffff',
      logging: false
    });

    console.log('[PDF] Content captured, validating...');

    // Validate canvas is not blank
    if (!canvas || canvas.width === 0 || canvas.height === 0) {
      console.error('[PDF] Canvas validation failed:', {
        exists: !!canvas,
        width: canvas?.width,
        height: canvas?.height
      });
      throw new Error(`Canvas capture failed - canvas is ${!canvas ? 'null' : 'zero-sized'}`);
    }

    // Validate canvas has actual content (not just blank)
    const imgData = canvas.toDataURL('image/png');
    if (!imgData || imgData === 'data:,' || imgData.length < 100) {
      console.error('[PDF] Canvas content validation failed:', {
        hasData: !!imgData,
        dataLength: imgData?.length,
        dataPreview: imgData?.substring(0, 50)
      });
      throw new Error('Canvas is blank - no content was captured');
    }

    console.log('[PDF] Canvas validated successfully, generating PDF...');

    // Create PDF with appropriate dimensions
    const imgWidth = dimensions.width;
    const imgHeight = dimensions.height;

    // Convert px to mm for jsPDF (1px ≈ 0.264583mm at 96 DPI)
    const pxToMm = 0.264583;
    const pdfWidth = imgWidth * pxToMm;
    const pdfHeight = imgHeight * pxToMm;

    // Determine orientation based on aspect ratio
    const orientation = imgWidth > imgHeight ? 'landscape' : 'portrait';

    // Create PDF with custom dimensions
    const pdf = new jsPDF({
      orientation: orientation,
      unit: 'mm',
      format: [pdfWidth, pdfHeight],
      compress: true
    });

    // Add image to PDF (imgData already captured during validation)
    try {
      pdf.addImage(imgData, 'PNG', 0, 0, pdfWidth, pdfHeight);
      console.log('[PDF] Image added to PDF successfully');
    } catch (error) {
      console.error('[PDF] Failed to add image to PDF:', error);
      throw new Error(`Failed to add image to PDF: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }

    // Generate filename with timestamp to ensure uniqueness
    const now = new Date();
    const timestamp = now.toISOString().slice(0, 10) + '_' +
                     now.toTimeString().slice(0, 8).replace(/:/g, '');
    const versionSuffix = version ? `_v${version}` : '';
    const finalFileName = fileName || `notebook_${notebookId.slice(0, 8)}${versionSuffix}_${timestamp}.pdf`;

    console.log(`[PDF] Saving as: ${finalFileName}`);

    // Convert to blob
    const blob = pdf.output('blob');

    // Save using Tauri (DMG) or browser download
    if (isTauriApp()) {
      const filePath = await saveBlobToFile(blob, finalFileName);
      console.log(`[PDF] Saved successfully to: ${filePath}`);
      return filePath;
    } else {
      // Browser download fallback
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = finalFileName;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      console.log(`[PDF] Downloaded as: ${finalFileName}`);
      return finalFileName; // Return filename for browser
    }

  } catch (error) {
    console.error('[PDF] Generation failed:', error);
    throw error;
  } finally {
    // Cleanup: remove iframe
    document.body.removeChild(iframe);
  }
}

/**
 * Generates PDF from the currently visible dashboard (faster, no reload)
 */
export async function generatePdfFromCurrentView(
  elementId: string = 'root',
  fileName?: string
): Promise<string> {
  try {
    const element = document.getElementById(elementId);
    if (!element) {
      throw new Error(`Element with id "${elementId}" not found`);
    }

    console.log('[PDF] Capturing current view...');

    // Capture the element
    const canvas = await html2canvas(element, {
      scale: 2,
      useCORS: true,
      allowTaint: false,
      backgroundColor: '#ffffff',
      logging: false
    });

    // Create PDF
    const imgWidth = canvas.width;
    const imgHeight = canvas.height;
    const pxToMm = 0.264583;
    const pdfWidth = imgWidth * pxToMm;
    const pdfHeight = imgHeight * pxToMm;
    const orientation = imgWidth > imgHeight ? 'landscape' : 'portrait';

    const pdf = new jsPDF({
      orientation: orientation,
      unit: 'mm',
      format: [pdfWidth, pdfHeight],
      compress: true
    });

    const imgData = canvas.toDataURL('image/png');
    pdf.addImage(imgData, 'PNG', 0, 0, pdfWidth, pdfHeight);

    // Save with timestamp to ensure uniqueness
    const now = new Date();
    const timestamp = now.toISOString().slice(0, 10) + '_' +
                     now.toTimeString().slice(0, 8).replace(/:/g, '');
    const finalFileName = fileName || `dashboard_${timestamp}.pdf`;
    const blob = pdf.output('blob');

    // Save using Tauri (DMG) or browser download
    if (isTauriApp()) {
      const filePath = await saveBlobToFile(blob, finalFileName);
      console.log(`[PDF] Saved successfully to: ${filePath}`);
      return filePath;
    } else {
      // Browser download fallback
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = finalFileName;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      console.log(`[PDF] Downloaded as: ${finalFileName}`);
      return finalFileName;
    }

  } catch (error) {
    console.error('[PDF] Generation failed:', error);
    throw error;
  }
}

/**
 * High-level wrapper with error handling and toast notifications
 * Returns the file path/name for use with download notifications
 */
export async function exportNotebookToPdf(
  notebookId: string,
  version?: number | null,
  fileName?: string
): Promise<string> {
  try {
    const filePath = await generatePdfFromNotebook({ notebookId, version, fileName });
    // Return filePath for download notification (caller will show notification)
    return filePath;
  } catch (error) {
    console.error('PDF export failed:', error);
    const message = error instanceof Error ? error.message : 'Unknown error';
    showToast.error(`Failed to export PDF: ${message}`);
    throw error;
  }
}
