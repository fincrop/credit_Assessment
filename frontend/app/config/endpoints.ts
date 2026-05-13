import { ApiEndpoint } from '../types/api';

const WEBHOOK_URL = `${process.env.NEXT_PUBLIC_APP_DOMAIN || 'http://localhost:3000'}/webhook/on-seek`;
const FARMERS_WEBHOOK_URL = `${process.env.NEXT_PUBLIC_APP_DOMAIN || 'http://localhost:3000'}/webhook/farmers/on-seek`;
const KDSS_WEBHOOK_URL = `${process.env.NEXT_PUBLIC_APP_DOMAIN || 'http://localhost:3000'}/webhook/kdss/on-seek`;

export const ENDPOINTS: ApiEndpoint[] = [
    { id: 'token', name: 'Token', method: 'POST', url: 'https://sandbox.agristack.gov.in/sandbox-api/nm/token' },
    { id: 'farmer-land-id', name: 'Farmer & Land Data by Farmer ID (i1001:o1004)', method: 'POST', url: 'https://sandbox.agristack.gov.in/sandbox-api/agristack/seek' },
    { id: 'land-parcel', name: 'Land Parcel by LGD & Survey (i1003:o1002)', method: 'POST', url: 'https://sandbox.agristack.gov.in/sandbox-api/agristack/seek' },
    { id: 'seek', name: 'Seek (i1004:o1007)', method: 'POST', url: 'https://sandbox.agristack.gov.in/sandbox-api/agristack/seek' },
    { id: 'krishi-dss-seek', name: 'Krishi DSS Seek', method: 'POST', url: 'https://sandbox.agristack.gov.in/sandbox-api/krishi-dss-seek' },
];

export interface EndpointConfig {
    headers: Record<string, string>;
    body: Record<string, unknown>;
    apiRoute: string;
}

export const getEndpointConfigs = (accessToken: string | null): Record<string, EndpointConfig> => ({
    token: {
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: {
            client_id: 'registry_sandbox',
            username: 'dev_fincrop',
            password: 'FinCrop@123',
            grant_type: 'password',
        },
        apiRoute: '/api/token',
    },
    'farmer-land-id': {
        headers: {
            'Content-Type': 'application/json',
            'Authorization': accessToken ? `Bearer ${accessToken}` : 'Bearer YOUR_TOKEN_HERE',
        },
        body: {
            header: {
                version: '0.1.0',
                sender_id: 'f20f0d3c-ccdb-4d96-ba7a-cf8476d7c15a',
                message_id: 'd63d5c31-6922-4357-9584-73c3d02a12ed',
                message_ts: '2025-05-07T05:00:10.003902+0530',
                sender_uri: WEBHOOK_URL,
                receiver_id: 'a5e89f01-519f-4378-93eb-2e1cbfcc512e',
                total_count: 1,
                is_msg_encrypted: false
            },
            message: {
                search_request: [
                    {
                        locale: 'en',
                        timestamp: '2025-05-07T05:00:10.003902+0530',
                        reference_id: 'c9ef573f-ffb7-48d3-89d0-ab7707cf8edd',
                        search_criteria: {
                            query: {
                                mapper_id: 'i1001:o1004',
                                query_name: 'agristack_croparea_v3_get_data',
                                query_params: [
                                    {
                                        farmer_id: '57272407248',
                                        aadhaar_type: 'E',
                                        state_lgd_code: '8'
                                    }
                                ]
                            },
                            consent: {},
                            reg_type: 'agristack_farmer',
                            pagination: {
                                page_size: 1000,
                                page_number: 1
                            },
                            query_type: 'namedQuery'
                        }
                    }
                ],
                transaction_id: ''
            },
            signature: 'Signature string'
        },
        apiRoute: '/api/agristack',
    },
    'land-parcel': {
        headers: {
            'Content-Type': 'application/json',
            'Authorization': accessToken ? `Bearer ${accessToken}` : 'Bearer YOUR_TOKEN_HERE',
        },
        body: {
            header: {
                version: '0.1.0',
                sender_id: 'f20f0d3c-ccdb-4d96-ba7a-cf8476d7c15a',
                message_id: 'd63d5c31-6922-4357-9584-73c3d02a12ed',
                message_ts: '2025-05-07T05:00:10.003902+0530',
                sender_uri: WEBHOOK_URL,
                receiver_id: 'a5e89f01-519f-4378-93eb-2e1cbfcc512e',
                total_count: 1,
                is_msg_encrypted: false
            },
            message: {
                search_request: [
                    {
                        locale: 'en',
                        timestamp: '2025-05-07T05:00:10.003902+0530',
                        reference_id: 'c9ef573f-ffb7-48d3-89d0-ab7707cf8edd',
                        search_criteria: {
                            query: {
                                mapper_id: 'i1003:o1002',
                                query_name: 'agristack_croparea_v3_get_data',
                                query_params: [
                                    {
                                        survey_number: '240',
                                        state_lgd_code: '8',
                                        village_lgd_code: '80048'
                                    }
                                ]
                            },
                            consent: {},
                            reg_type: 'agristack_farmer',
                            pagination: {
                                page_size: 1000,
                                page_number: 1
                            },
                            query_type: 'namedQuery'
                        }
                    }
                ],
                transaction_id: ''
            },
            signature: 'Signature string'
        },
        apiRoute: '/api/agristack',
    },
    seek: {
        headers: {
            'Content-Type': 'application/json',
            'Authorization': accessToken ? `Bearer ${accessToken}` : 'Bearer YOUR_TOKEN_HERE',
        },
        body: {
            header: {
                version: '0.1.0',
                sender_id: 'f20f0d3c-ccdb-4d96-ba7a-cf8476d7c15a',
                message_id: 'd63d5c31-6922-4357-9584-73c3d02a12ed',
                message_ts: '2025-05-07T05:00:10.003902+0530',
                sender_uri: FARMERS_WEBHOOK_URL,
                receiver_id: 'a5e89f01-519f-4378-93eb-2e1cbfcc512e',
                total_count: 1,
                is_msg_encrypted: false
            },
            message: {
                search_request: [
                    {
                        locale: 'en',
                        timestamp: '2025-05-07T05:00:10.003902+0530',
                        reference_id: 'c9ef573f-ffb7-48d3-89d0-ab7707cf8edd',
                        search_criteria: {
                            query: {
                                mapper_id: 'i1004:o1007',
                                query_name: 'agristack_croparea_v3_get_data',
                                query_params: [
                                    {
                                        farmer_identifier: {
                                            farmer_id: '10001921019'
                                        },
                                        aadhaar_type: 'E',
                                        state_lgd_code: '9',
                                        year: '2022-2023',
                                        season: 'Rabi'
                                    }
                                ]
                            },
                            consent: {},
                            reg_type: 'agristack_farmer',
                            pagination: {
                                page_size: 1000,
                                page_number: 1
                            },
                            query_type: 'namedQuery'
                        }
                    }
                ],
                transaction_id: 'c4c6f0a1-3b2e-4b99-9c2c-0c9a7c7a8f21'
            },
            signature: 'Signature string'
        },
        apiRoute: '/api/agristack',
    },
    'krishi-dss-seek': {
        headers: {
            'Content-Type': 'application/json',
            'Authorization': accessToken ? `Bearer ${accessToken}` : 'Bearer YOUR_TOKEN_HERE',
            'entity-id': 'f20f0d3c-ccdb-4d96-ba7a-cf8476d7c15a',
        },
        body: {
            signature: 'signature string',
            header: {
                version: '0.1.0',
                message_id: '3e0663a3-1a61-475e-8b40-0eb0b81ac4c7',
                message_ts: '2022-12-04T18:01:07+00:00',
                sender_id: 'f20f0d3c-ccdb-4d96-ba7a-cf8476d7c15a',
                sender_uri: KDSS_WEBHOOK_URL,
                receiver_id: '3e0663a3-1a61-475e-8b40-0eb0b81ac4c7',
                total_count: 1,
                is_msg_encrypted: false
            },
            message: {
                transaction_id: '3e0663a3-1a61-475e-8b40-0eb0b81ac4c7',
                search_request: [
                    {
                        reference_id: '3e0663a3-1a61-475e-8b40-0eb0b81ac4c7',
                        timestamp: '2022-12-04T17:20:07-04:00',
                        search_criteria: {
                            query_type: 'namedQuery',
                            reg_type: 'krishidss_user',
                            query: {
                                query_name: 'LayerInformation_i23_get_data',
                                mapper_id: 'i23:o23',
                                query_params: [
                                    {
                                        wkt: 'POLYGON ((81.629609 27.55974, 81.636636 27.55974, 81.636636 27.563069, 81.629609 27.563069, 81.629609 27.55974))',
                                        type: 'ALL'
                                    }
                                ]
                            },
                            sort: [
                                {
                                    attribute_name: 'string',
                                    sort_order: 'asc'
                                }
                            ],
                            pagination: {
                                page_size: 2000,
                                page_number: 1
                            }
                        },
                        locale: 'en'
                    }
                ]
            }
        },
        apiRoute: '/api/krishi-dss-seek',
    },
});
