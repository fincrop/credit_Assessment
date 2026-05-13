// API Types for AgriStack Sandbox

export interface ApiEndpoint {
    id: string;
    name: string;
    method: 'POST' | 'GET' | 'PUT' | 'DELETE';
    url: string;
    description?: string;
}

export interface HeaderParam {
    key: string;
    value: string;
    type: 'string' | 'required';
    description?: string;
}

export interface ApiRequest {
    headers: Record<string, string>;
    body: Record<string, unknown>;
}

export interface ApiResponse {
    success: boolean;
    data?: unknown;
    error?: {
        code: number;
        message: string;
    };
    statusCode: number;
    responseTime?: number;
}

// AgriStack specific types
export interface AgriStackHeader {
    version: string;
    sender_id: string;
    message_id: string;
    message_ts: string;
    sender_uri: string;
    receiver_id: string;
    total_count: number;
    is_msg_encrypted: boolean;
}

export interface FarmerIdentifier {
    farmer_id: string;
}

export interface QueryParams {
    farmer_identifier: FarmerIdentifier;
    aadhaar_type: string;
    state_lgd_code: string;
    year: string;
    season: string;
}

export interface SearchCriteria {
    query: {
        mapper_id: string;
        query_name: string;
        query_params: QueryParams[];
    };
    consent: Record<string, unknown>;
    reg_type: string;
    pagination: {
        page_size: number;
        page_number: number;
    };
    query_type: string;
}

export interface SearchRequest {
    locale: string;
    timestamp: string;
    reference_id: string;
    search_criteria: SearchCriteria;
}

export interface AgriStackRequestBody {
    header: AgriStackHeader;
    message: {
        search_request: SearchRequest[];
        transaction_id: string;
    };
    signature: string;
}
