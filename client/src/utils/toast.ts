import { toast, type ToastOptions } from 'react-toastify'

type ByaanToastOptions = ToastOptions & {
  progressStyle?: React.CSSProperties
}

const defaultOptions: ByaanToastOptions = {
  position: 'bottom-right',
  autoClose: 3000,
  hideProgressBar: false,
  closeOnClick: true,
  pauseOnHover: true,
  draggable: true,
  style: {
    backgroundColor: '#1a1a1a',
    color: '#ffffff',
    border: '1px solid #333333',
    borderRadius: '12px',
    padding: '16px 20px',
    fontSize: '14px',
    fontWeight: '500',
    boxShadow: '0 10px 40px rgba(0, 0, 0, 0.5), 0 2px 8px rgba(0, 0, 0, 0.3)',
    backdropFilter: 'blur(8px)'
  }
}

export const showToast = {
  success: (message: string, options?: ByaanToastOptions) => {
    toast.success(message, {
      ...defaultOptions,
      style: {
        ...defaultOptions.style,
        background: 'linear-gradient(135deg, #1a1a1a 0%, #0f2f1f 100%)',
        border: '1px solid #10b981',
        borderLeft: '4px solid #10b981'
      },
      progressStyle: {
        background: '#10b981'
      },
      ...options
    })
  },

  error: (message: string, options?: ByaanToastOptions) => {
    toast.error(message, {
      ...defaultOptions,
      style: {
        ...defaultOptions.style,
        background: 'linear-gradient(135deg, #1a1a1a 0%, #2f0f0f 100%)',
        border: '1px solid #ef4444',
        borderLeft: '4px solid #ef4444'
      },
      progressStyle: {
        background: '#ef4444'
      },
      ...options
    })
  },

  warning: (message: string, options?: ByaanToastOptions) => {
    toast.warning(message, {
      ...defaultOptions,
      style: {
        ...defaultOptions.style,
        background: 'linear-gradient(135deg, #1a1a1a 0%, #2f1f0f 100%)',
        border: '1px solid #f59e0b',
        borderLeft: '4px solid #f59e0b'
      },
      progressStyle: {
        background: '#f59e0b'
      },
      ...options
    })
  },

  info: (message: string, options?: ByaanToastOptions) => {
    toast.info(message, {
      ...defaultOptions,
      style: {
        ...defaultOptions.style,
        background: 'linear-gradient(135deg, #1a1a1a 0%, #0f1f2f 100%)',
        border: '1px solid #3b82f6',
        borderLeft: '4px solid #3b82f6'
      },
      progressStyle: {
        background: '#3b82f6'
      },
      ...options
    })
  },

  loading: (message: string, options?: ByaanToastOptions) => {
    return toast.loading(message, {
      ...defaultOptions,
      style: {
        ...defaultOptions.style,
        background: 'linear-gradient(135deg, #1a1a1a 0%, #1f1f1f 100%)',
        border: '1px solid #6b7280',
        borderLeft: '4px solid #6b7280'
      },
      progressStyle: {
        background: '#6b7280'
      },
      ...options
    })
  }
}
